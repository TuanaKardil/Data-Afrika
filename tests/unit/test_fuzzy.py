"""Unit tests for fuzzy matching utilities."""

from afrienrich.utils.fuzzy import classify_match, name_match_ratio


class TestNameMatchRatio:
    def test_exact_match(self) -> None:
        assert name_match_ratio("ACCRA FOODS", "ACCRA FOODS") == 100

    def test_partial_match(self) -> None:
        ratio = name_match_ratio("ACCRA FOODS LTD", "ACCRA FOODS LIMITED")
        assert ratio >= 70

    def test_completely_different(self) -> None:
        ratio = name_match_ratio("ACCRA FOODS", "NAIROBI STEEL")
        assert ratio < 60

    def test_suffix_stripped_match(self) -> None:
        ratio = name_match_ratio("TOTAL SARL", "TOTAL")
        assert ratio >= 80

    def test_case_insensitive(self) -> None:
        ratio = name_match_ratio("accra foods", "ACCRA FOODS")
        assert ratio == 100


class TestClassifyMatch:
    def test_exact_is_accept(self) -> None:
        assert classify_match("ACCRA FOODS", "ACCRA FOODS") == "accept"

    def test_high_ratio_is_accept(self) -> None:
        assert classify_match("ACCRA FOODS LTD", "ACCRA FOODS LIMITED") == "accept"

    def test_medium_ratio_is_ambiguous(self) -> None:
        result = classify_match("ACCRA FOODS", "ACCRA FRESH PRODUCE")
        assert result in ("ambiguous", "accept")

    def test_low_ratio_is_reject(self) -> None:
        assert classify_match("ACCRA FOODS", "NAIROBI STEEL WORKS") == "reject"
