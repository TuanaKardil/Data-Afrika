"""Confidence scoring: weighted average formula from docs/architecture.md."""

from __future__ import annotations

from ..models import CompanyQuery, EmailResult, PhoneResult, SourceResult
from ..utils.fuzzy import name_match_ratio


def score_phone(phone: PhoneResult, expected_iso2: str, found_name: str, queried_name: str) -> int:
    """Compute final confidence for a single phone result."""
    score = phone.confidence

    # Name match bonus/penalty
    ratio = name_match_ratio(queried_name, found_name) if found_name else 100
    if ratio < 80:
        score -= 15

    return max(0, min(100, score))


def score_email(email: EmailResult, found_name: str, queried_name: str) -> int:
    """Compute final confidence for a single email result."""
    score = email.confidence

    ratio = name_match_ratio(queried_name, found_name) if found_name else 100
    if ratio < 80:
        score -= 15

    return max(0, min(100, score))


def compute_overall_confidence(
    source_results: list[SourceResult],
    company: CompanyQuery,
    phones_validated: list[PhoneResult],
    emails_validated: list[EmailResult],
    found_name: str = "",
) -> int:
    """Compute overall row-level confidence score."""
    if not phones_validated and not emails_validated:
        return 0

    # Base: average of individual confidences
    all_scores = [p.confidence for p in phones_validated] + [e.confidence for e in emails_validated]
    base = sum(all_scores) // len(all_scores) if all_scores else 0

    # Phone source tracking for Fix 7 bonuses/penalties
    phone_sources: dict[str, set[str]] = {}
    for r in source_results:
        for p in r.phones:
            phone_sources.setdefault(p.number, set()).add(r.source_name)

    # Fix 7: +10 if any phone confirmed by 2+ independent sources
    if any(len(srcs) >= 2 for srcs in phone_sources.values()):
        base += 10

    # Fix 7: -5 per phone from a single source (unconfirmed)
    single_source_phones = sum(
        1 for p in phones_validated if len(phone_sources.get(p.number, set())) < 2
    )
    base -= single_source_phones * 5

    # Validator bonuses (MX pass)
    if any(e.validation_level in ("mx", "smtp") for e in emails_validated):
        base += 5

    # Name mismatch penalty
    ratio = name_match_ratio(company.importer_name, found_name) if found_name else 100
    if ratio < 80:
        base -= 15

    return max(0, min(100, base))


def deduplicate_phones(phones: list[PhoneResult]) -> list[PhoneResult]:
    """Remove duplicate E.164 numbers, keeping the highest-confidence entry."""
    seen: dict[str, PhoneResult] = {}
    for p in phones:
        if p.number not in seen or p.confidence > seen[p.number].confidence:
            seen[p.number] = p
    return sorted(seen.values(), key=lambda x: x.confidence, reverse=True)[:8]


def deduplicate_emails(emails: list[EmailResult]) -> list[EmailResult]:
    """Remove duplicate email addresses, keeping the highest-confidence entry."""
    seen: dict[str, EmailResult] = {}
    for e in emails:
        addr = e.address.lower()
        if addr not in seen or e.confidence > seen[addr].confidence:
            seen[addr] = e
    return sorted(seen.values(), key=lambda x: x.confidence, reverse=True)[:5]
