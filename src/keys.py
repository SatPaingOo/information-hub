"""information-hub — multi API key manager.

Rotates across GEMINI_API_KEYS with budget tracking in registry/keys.json.

Strategies:
  round_robin  = pick keys in sequence by call count
  least_used   = pick the key with the fewest calls (then fewest errors)

Key policy (config.gemini.key_policy):
  rotate_on_error      = HTTP statuses that trigger rotating to the next key
  max_errors_per_key   = consecutive/rolling errors that disable a key for the run
"""

from __future__ import annotations

import threading
from typing import Any

from src.config import GeminiConfig, KeyPolicy
from src.registry import Registry


class KeyManager:
    def __init__(self, gemini: GeminiConfig, registry: Registry):
        self.gemini = gemini
        self.registry = registry
        self._lock = threading.Lock()
        self.keys = gemini.api_keys()

    # ---- public -------------------------------------------------------
    def pick(self) -> str:
        """Return the key to use next, or raise if none available."""
        if not self.keys:
            raise KeyManagerError("no API keys configured (set GEMINI_API_KEYS)")
        with self._lock:
            available = self._available_keys()
            if not available:
                raise KeyManagerError("all API keys failed (max_errors_per_key reached)")
            chosen = self._select(available)
            self.registry.record_key_use(chosen)
            return chosen

    def report(self, key: str, status_code: int | None, error: bool) -> None:
        """Record the outcome of a call against a key."""
        with self._lock:
            self.registry.record_key_result(
                key, status_code=status_code, error=error,
                max_errors=self.gemini.key_policy.max_errors_per_key,
            )

    def should_rotate(self, key: str, status_code: int | None) -> bool:
        """True if this error status should move to another key."""
        return status_code in self.gemini.key_policy.rotate_on_error

    # ---- internals -----------------------------------------------------
    def _available_keys(self) -> list[str]:
        policy = self.gemini.key_policy
        out: list[str] = []
        for key in self.keys:
            stats = self.registry.key_stats(key)
            if not stats.get("failed", False) and stats.get("errors", 0) < policy.max_errors_per_key:
                out.append(key)
        return out

    def _select(self, available: list[str]) -> str:
        if self.gemini.key_strategy == "round_robin":
            return min(available, key=lambda k: (
                self.registry.key_stats(k).get("calls", 0), self.registry.key_stats(k).get("last_used", "")))
        # least_used
        return min(available, key=lambda k: (
            self.registry.key_stats(k).get("calls", 0),
            self.registry.key_stats(k).get("errors", 0),
        ))


class KeyManagerError(RuntimeError):
    pass
