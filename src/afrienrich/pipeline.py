"""Row orchestrator: runs sources in tier order, validates, scores, and writes results."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog
import yaml

from .ai.judge import Judge
from .cache import Cache
from .excel.writer import StagingWriter
from .models import (
    AuditEntry,
    CompanyQuery,
    EmailResult,
    EnrichedRow,
    PhoneResult,
    SourceResult,
)
from .normalize import clean_country, extract_embedded_phone, normalize_name
from .scoring.scorer import (
    compute_overall_confidence,
    deduplicate_emails,
    deduplicate_phones,
)
from .sources.base import BaseSource
from .sources.email_prober import EmailProberSource
from .sources.facebook import FacebookSource
from .sources.go_africa_online import GoAfricaOnlineSource
from .sources.serper_maps import SerperMapsSource
from .sources.serper_web import SerperWebSource
from .sources.website_crawler import WebsiteCrawlerSource
from .utils.fuzzy import classify_match
from .validators.duplicate_detector import DuplicateDetector
from .validators.email_validator import validate_email_result
from .validators.phone_validator import validate_phones
from .validators.quality_filters import (
    QualityFilter,
    validate_email_domain_match,
    validate_email_format,
    validate_website_country,
)

log = structlog.get_logger()

_COUNTRIES_PATH = Path(__file__).parent.parent.parent / "config" / "countries.yaml"


def _load_country_config() -> dict[str, dict[str, object]]:
    with open(_COUNTRIES_PATH) as f:
        data: dict[str, object] = yaml.safe_load(f) or {}
    countries = data.get("countries", [])
    if not isinstance(countries, list):
        return {}
    return {str(c["iso2"]): c for c in countries if isinstance(c, dict)}


_COUNTRY_CONFIG: dict[str, dict[str, object]] = {}


def _get_country_config() -> dict[str, dict[str, object]]:
    global _COUNTRY_CONFIG
    if not _COUNTRY_CONFIG:
        _COUNTRY_CONFIG = _load_country_config()
    return _COUNTRY_CONFIG


@dataclass
class RunConfig:
    output_path: Path
    resume: bool = True
    smtp_probe: bool = False
    no_cache: bool = False
    high_quality: bool = False
    max_rows: int | None = None
    concurrency: int = 5
    sources_whitelist: list[str] = field(default_factory=list)
    sources_blacklist: list[str] = field(default_factory=list)


def _build_sources(config: RunConfig) -> list[BaseSource]:
    # EmailProberSource is excluded by default: it derives pattern addresses (info@, contact@)
    # without direct observation. Enable explicitly via --sources email_prober.
    all_sources: list[BaseSource] = [
        SerperWebSource(),
        GoAfricaOnlineSource(),
        SerperMapsSource(),
        WebsiteCrawlerSource(),
        FacebookSource(),
    ]
    if "email_prober" in config.sources_whitelist:
        all_sources.append(EmailProberSource())
    sources = []
    for s in all_sources:
        if config.sources_whitelist and s.name not in config.sources_whitelist:
            continue
        if s.name in config.sources_blacklist:
            continue
        sources.append(s)
    return sorted(sources, key=lambda x: x.tier)


def _stop_early(phones: list[PhoneResult], emails: list[EmailResult], website: str | None) -> bool:
    return len(phones) >= 3 and len(emails) >= 1 and website is not None


async def process_row(
    company: CompanyQuery,
    config: RunConfig,
    cache: Cache,
    staging_writer: StagingWriter,
    duplicate_detector: DuplicateDetector,
    judge: Judge,
    quality_filter: QualityFilter,
) -> tuple[EnrichedRow, list[AuditEntry]]:
    t_start = time.monotonic()
    country_cfg = _get_country_config()
    iso2 = clean_country(company.country)
    cfg = country_cfg.get(iso2, {})
    primary_language = cfg.get("primary_language", "en")

    # Preflight normalisation
    normalized = normalize_name(company.importer_name, iso2)
    embedded_phone = extract_embedded_phone(company.importer_name)
    company = company.model_copy(
        update={
            "country": iso2,
            "cleaned_name": normalized,
            "extra": {
                **company.extra,
                "_primary_language": primary_language,
                "_embedded_phone": embedded_phone,
            },
        }
    )

    # Cache check
    if not config.no_cache:
        cached = cache.get_row(iso2, normalized)
        if cached is not None:
            log.debug("cache_hit", company=company.importer_name)
            return cached, []

    sources = _build_sources(config)
    all_source_results: list[SourceResult] = []
    all_audit_entries: list[AuditEntry] = []
    collected_phones: list[PhoneResult] = []
    collected_emails: list[EmailResult] = []
    discovered_website: str | None = None
    discovered_domain: str | None = None
    ai_row_cost: float = 0.0

    for source in sources:
        # Propagate discovered data for dependent sources
        extra_update: dict[str, str] = {}
        if discovered_website:
            extra_update["_discovered_website"] = discovered_website
        if discovered_domain:
            extra_update["_discovered_domain"] = discovered_domain
        if extra_update:
            company = company.model_copy(update={"extra": {**company.extra, **extra_update}})

        try:
            result = await source.search(company)
        except Exception as exc:
            log.warning(
                "source_error",
                source=source.name,
                company=company.importer_name,
                error=str(exc),
            )
            all_audit_entries.append(
                AuditEntry(
                    audit_id=f"err-{source.name}",
                    source_name=source.name,
                    query=company.importer_name,
                    status="error",
                    latency_ms=0,
                    notes=str(exc),
                )
            )
            continue

        all_source_results.append(result)
        all_audit_entries.extend(result.audit_entries)
        collected_phones.extend(result.phones)
        collected_emails.extend(result.emails)

        if result.website and not discovered_website:
            discovered_website = result.website
            from .extractors.website import extract_domain
            discovered_domain = extract_domain(result.website)

        if _stop_early(collected_phones, collected_emails, discovered_website):
            log.debug("stop_early", source=source.name, company=company.importer_name)
            break

    # Fuzzy match + AI judge on ambiguous prose
    match_class = classify_match(company.importer_name, normalized)
    if match_class == "ambiguous" and all_source_results:
        # Gather unstructured text and try to extract more contacts
        all_text = " ".join(
            ae.notes for sr in all_source_results for ae in sr.audit_entries if ae.notes
        )
        if all_text.strip():
            extract_result, ai_audit, cost = await judge.extract_contact_from_prose(
                company.importer_name, iso2, all_text, ai_row_cost
            )
            ai_row_cost += cost
            all_audit_entries.append(ai_audit)
            if extract_result:
                from .extractors.email import extract_emails
                from .extractors.phone import extract_phones
                extra_phones = extract_phones(
                    " ".join(extract_result.phones), iso2, "ai_judge", 60
                )
                extra_emails = extract_emails(
                    " ".join(extract_result.emails), "ai_judge", 60
                )
                collected_phones.extend(extra_phones)
                collected_emails.extend(extra_emails)

    # Embedded phone from name field
    if embedded_phone:
        from .extractors.phone import extract_phones
        ep_list = extract_phones(embedded_phone, iso2, "embedded_name", 70)
        collected_phones.extend(ep_list)

    # --- Quality gate: phones ---
    # 1. Structural validation (E.164, region match)
    validated_phones, whatsapp_numbers = validate_phones(collected_phones, iso2)
    # 2. Country-prefix whitelist filter
    prefix_ok_phones, phone_filter_notes = quality_filter.filter_phones(validated_phones, iso2)

    # --- Quality gate: emails ---
    # 1. Format check before expensive MX lookup
    format_ok_emails: list[EmailResult] = []
    email_filter_notes: list[str] = []
    for email in collected_emails:
        fmt_ok, fmt_note = validate_email_format(email)
        if fmt_ok:
            format_ok_emails.append(email)
        else:
            email_filter_notes.append(fmt_note)

    # 2. MX validation (only format-valid addresses)
    validated_emails: list[EmailResult] = []
    smtp = config.smtp_probe
    for email in format_ok_emails:
        validated = await asyncio.get_event_loop().run_in_executor(
            None, validate_email_result, email, smtp
        )
        validated_emails.append(validated)

    # --- Deduplication ---
    final_phones = deduplicate_phones(prefix_ok_phones)
    deduped_emails = deduplicate_emails(validated_emails)

    # --- Email uniqueness (cross-company) ---
    unique_emails: list[EmailResult] = []
    for email in deduped_emails:
        uniq_ok, uniq_note = quality_filter.check_email_uniqueness(email)
        if uniq_ok:
            unique_emails.append(email)
        else:
            email_filter_notes.append(uniq_note)
    quality_filter.register_emails(unique_emails)

    # --- Email domain / foreign-country soft warnings ---
    domain_notes: list[str] = []
    email_penalty = 0
    for email in unique_emails:
        pen, d_note = validate_email_domain_match(email, company.importer_name, iso2)
        if d_note:
            domain_notes.append(d_note)
            email_penalty += pen

    final_emails = unique_emails

    # --- Website country-path check ---
    website_penalty, website_note = validate_website_country(discovered_website or "", iso2)

    # Duplicate phone detection across run
    for p in final_phones:
        duplicate_detector.record(p.number, company.row_index)

    # Confidence scoring
    overall_confidence = compute_overall_confidence(
        all_source_results,
        company,
        final_phones,
        final_emails,
        found_name=normalized,
    )
    # Apply quality penalties
    overall_confidence = max(0, overall_confidence - email_penalty - website_penalty)

    # Determine search_status
    if final_phones or final_emails or discovered_website:
        if final_phones and final_emails and discovered_website:
            search_status = "enriched"
        else:
            search_status = "partial"
    else:
        search_status = "not_found"

    # Build whatsapp_number: first from whatsapp_numbers list or from social
    whatsapp_str = whatsapp_numbers[0] if whatsapp_numbers else ""
    social_urls_list = [sr.social_urls for sr in all_source_results if sr.social_urls]
    facebook_url = next((s.facebook for s in social_urls_list if s.facebook), "")
    linkedin_url = next((s.linkedin for s in social_urls_list if s.linkedin), "")
    instagram_url = next((s.instagram for s in social_urls_list if s.instagram), "")
    if not whatsapp_str:
        whatsapp_str = next((s.whatsapp for s in social_urls_list if s.whatsapp), "") or ""

    address_str = next((sr.address for sr in all_source_results if sr.address), "") or ""
    gps = next((sr.gps for sr in all_source_results if sr.gps), None)

    audit_ids_str = ",".join(ae.audit_id for ae in all_audit_entries)

    def _phone_field(phones: list[PhoneResult], i: int, attr: str) -> str:
        if i < len(phones):
            return str(getattr(phones[i], attr, ""))
        return ""

    def _phone_int(phones: list[PhoneResult], i: int, attr: str) -> int:
        if i < len(phones):
            return int(getattr(phones[i], attr, 0))
        return 0

    def _email_field(emails: list[EmailResult], i: int, attr: str) -> str:
        if i < len(emails):
            return str(getattr(emails[i], attr, ""))
        return ""

    def _email_int(emails: list[EmailResult], i: int, attr: str) -> int:
        if i < len(emails):
            return int(getattr(emails[i], attr, 0))
        return 0

    enriched = EnrichedRow(
        importer_name=company.importer_name,
        country=company.country,
        hs_code=company.hs_code,
        category_hint=company.category_hint,
        cleaned_name=company.cleaned_name,
        extra=company.extra,
        row_index=company.row_index,
        normalized_name=normalized,
        country_iso2=iso2,
        phone_1=_phone_field(final_phones, 0, "number"),
        phone_1_source=_phone_field(final_phones, 0, "source"),
        phone_1_confidence=_phone_int(final_phones, 0, "confidence"),
        phone_1_type=_phone_field(final_phones, 0, "phone_type"),
        phone_1_audit_id=_phone_field(final_phones, 0, "audit_id"),
        phone_2=_phone_field(final_phones, 1, "number"),
        phone_2_source=_phone_field(final_phones, 1, "source"),
        phone_2_confidence=_phone_int(final_phones, 1, "confidence"),
        phone_2_type=_phone_field(final_phones, 1, "phone_type"),
        phone_2_audit_id=_phone_field(final_phones, 1, "audit_id"),
        phone_3=_phone_field(final_phones, 2, "number"),
        phone_3_source=_phone_field(final_phones, 2, "source"),
        phone_3_confidence=_phone_int(final_phones, 2, "confidence"),
        phone_3_type=_phone_field(final_phones, 2, "phone_type"),
        phone_3_audit_id=_phone_field(final_phones, 2, "audit_id"),
        phone_4=_phone_field(final_phones, 3, "number"),
        phone_4_source=_phone_field(final_phones, 3, "source"),
        phone_4_confidence=_phone_int(final_phones, 3, "confidence"),
        phone_4_type=_phone_field(final_phones, 3, "phone_type"),
        phone_4_audit_id=_phone_field(final_phones, 3, "audit_id"),
        phone_5=_phone_field(final_phones, 4, "number"),
        phone_5_source=_phone_field(final_phones, 4, "source"),
        phone_5_confidence=_phone_int(final_phones, 4, "confidence"),
        phone_5_type=_phone_field(final_phones, 4, "phone_type"),
        phone_5_audit_id=_phone_field(final_phones, 4, "audit_id"),
        email_1=_email_field(final_emails, 0, "address"),
        email_1_source=_email_field(final_emails, 0, "source"),
        email_1_confidence=_email_int(final_emails, 0, "confidence"),
        email_1_validation=_email_field(final_emails, 0, "validation_level"),
        email_1_audit_id=_email_field(final_emails, 0, "audit_id"),
        email_2=_email_field(final_emails, 1, "address"),
        email_2_source=_email_field(final_emails, 1, "source"),
        email_2_confidence=_email_int(final_emails, 1, "confidence"),
        email_2_validation=_email_field(final_emails, 1, "validation_level"),
        email_2_audit_id=_email_field(final_emails, 1, "audit_id"),
        website=discovered_website or "",
        facebook_url=facebook_url or "",
        linkedin_url=linkedin_url or "",
        instagram_url=instagram_url or "",
        whatsapp_number=whatsapp_str,
        address=address_str,
        gps_lat=gps.lat if gps else None,
        gps_lon=gps.lon if gps else None,
        search_status=search_status,
        overall_confidence=overall_confidence,
        notes=_build_notes(
            ai_row_cost,
            phone_filter_notes,
            email_filter_notes,
            domain_notes,
            website_note,
        ),
        audit_ids=audit_ids_str,
    )

    # Cache write
    if not config.no_cache:
        cache.set_row(iso2, normalized, enriched)

    staging_writer.write_row(enriched)

    elapsed = int((time.monotonic() - t_start) * 1000)
    rejected_phones = len(phone_filter_notes)
    rejected_emails = len(email_filter_notes)
    log.info(
        "row_processed",
        company=company.importer_name,
        status=search_status,
        phones=len(final_phones),
        emails=len(final_emails),
        confidence=overall_confidence,
        elapsed_ms=elapsed,
        rejected_fields={
            "phones": rejected_phones,
            "emails": rejected_emails,
            "website_penalty": website_penalty,
        },
    )
    return enriched, all_audit_entries


def _build_notes(
    ai_cost: float,
    phone_notes: list[str],
    email_notes: list[str],
    domain_notes: list[str],
    website_note: str,
) -> str:
    parts: list[str] = []
    if ai_cost:
        parts.append(f"ai_cost=${ai_cost:.5f}")
    parts.extend(phone_notes)
    parts.extend(email_notes)
    parts.extend(domain_notes)
    if website_note:
        parts.append(website_note)
    return "; ".join(parts)
