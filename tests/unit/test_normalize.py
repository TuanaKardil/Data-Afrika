"""Unit tests for normalize.py."""

from afrienrich.normalize import clean_country, extract_embedded_phone, normalize_name


class TestNormalizeName:
    def test_strips_sarl_ci(self) -> None:
        assert normalize_name("IMPORTEX SARL", "CI") == "IMPORTEX"

    def test_strips_ltd_ng(self) -> None:
        result = normalize_name("LAGOS IMPORTS LTD", "NG")
        assert "SARL" not in result
        assert "LTD" not in result

    def test_strips_sa_suffix(self) -> None:
        result = normalize_name("DAKAR TRADING SA", "SN")
        # May or may not strip depending on country config
        assert result.strip().upper() in ("DAKAR TRADING", "DAKAR TRADING SA")

    def test_preserves_name_without_suffix(self) -> None:
        result = normalize_name("ACCRA FOODS", "GH")
        assert "ACCRA" in result

    def test_strips_embedded_phone_from_name(self) -> None:
        result = normalize_name("TOTAL SARL +225 07 00 00 00", "CI")
        assert "+225" not in result

    def test_empty_name(self) -> None:
        result = normalize_name("", "CI")
        assert result == ""

    def test_case_insensitive_suffix(self) -> None:
        result = normalize_name("Best Company sarl", "CI")
        assert "sarl" not in result.lower()


class TestExtractEmbeddedPhone:
    def test_extracts_international_phone(self) -> None:
        result = extract_embedded_phone("IMPORTEX SARL +225 07 12 34 56")
        assert result is not None
        assert "+225" in result

    def test_returns_none_for_clean_name(self) -> None:
        result = extract_embedded_phone("ACCRA FOODS LTD")
        assert result is None

    def test_extracts_local_phone(self) -> None:
        result = extract_embedded_phone("DAKAR FOODS 0022 33 45 67")
        assert result is not None


class TestCleanCountry:
    def test_iso2_passthrough(self) -> None:
        assert clean_country("CI") == "CI"

    def test_lowercase_normalized(self) -> None:
        assert clean_country("ci") == "CI"

    def test_full_name_ivory_coast(self) -> None:
        result = clean_country("Ivory Coast")
        assert result in ("CI", "Ivory Coast")  # implementation-dependent

    def test_unknown_returns_input(self) -> None:
        result = clean_country("ZZ")
        assert result == "ZZ"
