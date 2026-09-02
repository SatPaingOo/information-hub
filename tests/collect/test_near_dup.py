"""Near-duplicate containment tests (cross-day re-fetch / update headlines)."""
from src.collect.dedup import token_containment


def test_containment_catches_shorter_retitle():
    # same paper re-fetched next day with a shorter title
    a = "Toward Compact Data from Big Data: A Novel Approach to Dataset Optimization"
    b = "Toward Compact Data from Big Data"
    assert token_containment(a, b) >= 0.99


def test_containment_catches_update_variant():
    # casualty-count update differs by one token
    a = "Russian double-tap drone strike kills 15 in Ukrainian mall"
    b = "Russian double-tap drone strike kills 16 in Ukrainian mall"
    assert token_containment(a, b) >= 0.9


def test_containment_low_for_distinct():
    a = "Israel re-establishes closed West Bank settlement"
    b = "TikTok to pay $400m to US in child privacy settlement"
    assert token_containment(a, b) < 0.4
