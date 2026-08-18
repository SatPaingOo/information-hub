"""Tests for select-output validation (out-of-range index protection).

Free-tier models occasionally return ``selected`` indices past the
candidate list; an unguarded ``candidates[idx]`` crashed the whole
collect run on 2026-08-18 (IndexError) and lost unsaved quota state.
"""

from __future__ import annotations

from src.main import _valid_select_indices


def test_valid_indices_pass_through():
    assert _valid_select_indices([0, 1, 2], 8) == [0, 1, 2]


def test_out_of_range_indices_dropped():
    assert _valid_select_indices([0, 8, 9, -1], 8) == [0]


def test_non_integer_indices_dropped():
    assert _valid_select_indices(["1", "2", "three", None], 8) == [1, 2]


def test_empty_and_none_input():
    assert _valid_select_indices([], 8) == []
    assert _valid_select_indices(None, 8) == []


def test_zero_candidates():
    assert _valid_select_indices([0], 0) == []
