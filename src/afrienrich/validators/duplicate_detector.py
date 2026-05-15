"""Detect phone numbers that appear across many unrelated rows (probable intermediary/agent)."""

from __future__ import annotations

from collections import Counter

INTERMEDIARY_THRESHOLD = 5


class DuplicateDetector:
    def __init__(self, threshold: int = INTERMEDIARY_THRESHOLD) -> None:
        self._threshold = threshold
        self._phone_counts: Counter[str] = Counter()

    def record(self, phone: str, row_index: int = 0) -> None:
        """Record a phone number seen for a given row."""
        self._phone_counts[phone] += 1

    def is_probable_intermediary(self, phone: str) -> bool:
        return self._phone_counts[phone] >= self._threshold

    def get_flagged(self) -> set[str]:
        """Return all phone numbers that exceed the threshold."""
        return {p for p, count in self._phone_counts.items() if count >= self._threshold}

    def flag_note(self, phone: str) -> str:
        count = self._phone_counts[phone]
        if count >= self._threshold:
            return f"probable_intermediary (seen {count}x in this run)"
        return ""
