"""Unit tests for confidence scoring."""

from afrienrich.models import CompanyQuery, EmailResult, PhoneResult, SourceResult
from afrienrich.scoring.scorer import (
    compute_overall_confidence,
    deduplicate_emails,
    deduplicate_phones,
)


def _make_company(name: str = "TEST CO", country: str = "CI") -> CompanyQuery:
    return CompanyQuery(importer_name=name, country=country)


def _make_phone(number: str, confidence: int, source: str = "test") -> PhoneResult:
    return PhoneResult(number=number, source=source, confidence=confidence)


def _make_email(
    address: str, confidence: int, source: str = "test", level: str = "mx"
) -> EmailResult:
    return EmailResult(
        address=address, source=source, confidence=confidence, validation_level=level
    )


class TestComputeOverallConfidence:
    def test_zero_when_no_data(self) -> None:
        company = _make_company()
        assert compute_overall_confidence([], company, [], []) == 0

    def test_basic_score(self) -> None:
        company = _make_company()
        phones = [_make_phone("+2250701234567", 70)]
        emails = [_make_email("test@example.com", 65)]
        result = [SourceResult(source_name="test", phones=phones, emails=emails)]
        score = compute_overall_confidence(result, company, phones, emails)
        assert 50 <= score <= 100

    def test_multi_source_bonus(self) -> None:
        company = _make_company()
        phones = [_make_phone("+2250701234567", 70, "source_a")]
        emails = [_make_email("test@example.com", 65, "source_b")]
        results = [
            SourceResult(source_name="source_a", phones=phones),
            SourceResult(source_name="source_b", emails=emails),
        ]
        score = compute_overall_confidence(results, company, phones, emails)
        # Should have +10 multi-source bonus applied
        single_result = [SourceResult(source_name="source_a", phones=phones, emails=emails)]
        single_score = compute_overall_confidence(single_result, company, phones, emails)
        assert score >= single_score

    def test_mx_pass_bonus(self) -> None:
        company = _make_company()
        emails = [_make_email("test@example.com", 70, level="mx")]
        results = [SourceResult(source_name="test", emails=emails)]
        score_mx = compute_overall_confidence(results, company, [], emails)

        emails_syntax = [_make_email("test@example.com", 70, level="syntax")]
        results_syntax = [SourceResult(source_name="test", emails=emails_syntax)]
        score_syntax = compute_overall_confidence(results_syntax, company, [], emails_syntax)
        assert score_mx >= score_syntax


class TestDeduplicatePhones:
    def test_keeps_highest_confidence(self) -> None:
        phones = [
            _make_phone("+2250701234567", 60),
            _make_phone("+2250701234567", 80),
        ]
        result = deduplicate_phones(phones)
        assert len(result) == 1
        assert result[0].confidence == 80

    def test_caps_at_five(self) -> None:
        phones = [_make_phone(f"+22507{i:07d}", 70) for i in range(7)]
        result = deduplicate_phones(phones)
        assert len(result) == 5

    def test_preserves_different_numbers(self) -> None:
        phones = [_make_phone(f"+22507{i:07d}", 70) for i in range(3)]
        result = deduplicate_phones(phones)
        assert len(result) == 3


class TestDeduplicateEmails:
    def test_case_insensitive_dedup(self) -> None:
        emails = [
            _make_email("Test@Example.com", 70),
            _make_email("test@example.com", 80),
        ]
        result = deduplicate_emails(emails)
        assert len(result) == 1
        assert result[0].confidence == 80

    def test_caps_at_two(self) -> None:
        emails = [_make_email(f"user{i}@example.com", 70) for i in range(4)]
        result = deduplicate_emails(emails)
        assert len(result) == 2
