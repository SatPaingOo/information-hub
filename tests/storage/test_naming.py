"""Tests for src.storage.naming — filename helpers shared by writers + renderer."""

from __future__ import annotations

from src.storage.naming import safe_name, taxo_name


def test_taxo_name_case_folds_so_variants_collapse():
    # The same taxonomy topic arrives from the LLM in varying capitalizations;
    # every variant must map to ONE note file, or case-variant paths pile up and
    # collide on case-insensitive filesystems (Windows).
    assert (
        taxo_name("International Law")
        == taxo_name("International law")
        == taxo_name("international law")
        == "international_law"
    )
    assert taxo_name("Global") == taxo_name("global") == "global"
    assert taxo_name("  Energy Security  ") == "energy_security"


def test_taxo_name_uses_safe_name_transform():
    # Non-alphanumerics (except - and _) become underscores, like safe_name.
    assert taxo_name("AI/ML & Robotics") == "ai_ml___robotics"
    assert taxo_name("Open-source_software") == "open-source_software"


def test_safe_name_preserves_case_for_entities():
    # Entity notes are proper nouns and have no collision problem — their casing
    # must be preserved (taxo_name must NOT be applied to them).
    assert safe_name("Donald Trump") == "Donald_Trump"
    assert safe_name("NATO") == "NATO"
