"""Fuzzy name matching utilities used to accept, flag, or reject search results."""

from __future__ import annotations

from rapidfuzz import fuzz

ACCEPT_THRESHOLD = 80
AMBIGUOUS_THRESHOLD = 60


def name_match_ratio(queried: str, found: str) -> float:
    """Return token-set ratio (0–100) between two company names."""
    return fuzz.token_set_ratio(queried.lower(), found.lower())


def classify_match(queried: str, found: str) -> str:
    """Return 'accept', 'ambiguous', or 'reject' based on name similarity."""
    ratio = name_match_ratio(queried, found)
    if ratio >= ACCEPT_THRESHOLD:
        return "accept"
    if ratio >= AMBIGUOUS_THRESHOLD:
        return "ambiguous"
    return "reject"
