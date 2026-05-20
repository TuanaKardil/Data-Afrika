"""Row orchestrator: runs sources in tier order, validates, scores, and writes results."""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
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
from .sources.facebook import FacebookSource, get_facebook_tier
from .sources.go_africa_online import GoAfricaOnlineSource
from .sources.serper_maps import SerperMapsSource
from .sources.serper_web import SerperWebSource
from .sources.website_crawler import WebsiteCrawlerSource
from .utils.fuzzy import classify_match
from .validators.duplicate_detector import DuplicateDetector
from .validators.email_domain_verifier import (
    DomainVerificationResult,
    verify_email_domain,
    verify_website_country,
)
from .validators.email_validator import validate_email_result
from .validators.phone_validator import validate_phones
from .validators.quality_filters import (
    QualityFilter,
    check_email_role,
    compute_collision_risk,
    validate_email_domain_match,
    validate_email_format,
    validate_website_url,
)

log = structlog.get_logger()

_COUNTRIES_PATH = Path(__file__).parent.parent.parent / "config" / "countries.yaml"


def _strip_www(netloc: str) -> str:
    return netloc[4:] if netloc.startswith("www.") else netloc


def _load_country_config() -> dict[str, dict[str, object]]:
    with open(_COUNTRIES_PATH) as f:
        data: dict[str, object] = yaml.safe_load(f) or {}
    # YAML is a flat dict keyed by ISO2 (e.g. {BF: {...}, CI: {...}})
    return {k: v for k, v in data.items() if isinstance(v, dict)}


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


def _build_sources(config: RunConfig, iso2: str = "") -> list[BaseSource]:
    # EmailProberSource is excluded by default: it derives pattern addresses (info@, contact@)
    # without direct observation. Enable explicitly via --sources email_prober.
    fb_source = FacebookSource()
    # Fix 12: set Facebook tier from country config (tier 2 for West/Central Africa)
    if iso2:
        fb_source.tier = get_facebook_tier(iso2)

    all_sources: list[BaseSource] = [
        SerperWebSource(),
        GoAfricaOnlineSource(),
        SerperMapsSource(),
        WebsiteCrawlerSource(),
        fb_source,
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


def _find_subsidiary_url(
    source_results: list[SourceResult],
    company_name: str,
    parent_netloc: str,
) -> str:
    """Fix 15 — find a subsidiary domain URL from candidate_urls collected during search.

    Looks for non-parent URLs whose domain fuzzy-matches the company name (>= 60).
    """
    from rapidfuzz import fuzz

    name_clean = re.sub(
        r"\b(sarl|sa|sas|gie|snc|suarl|ltd|plc|burkina|faso)\b",
        "", company_name, flags=re.IGNORECASE,
    )
    name_norm = re.sub(r"[^a-z0-9]", "", name_clean.lower())
    _skip = {"facebook", "linkedin", "instagram", "google", "goafricaonline", "kinamap"}

    for sr in source_results:
        for url in sr.candidate_urls:
            try:
                netloc = _strip_www(urlparse(url).netloc)
            except Exception:
                continue
            if not netloc or netloc == parent_netloc:
                continue
            if any(s in netloc for s in _skip):
                continue
            domain_base = re.sub(r"\.[^.]+$", "", netloc)  # strip TLD
            domain_norm = re.sub(r"[^a-z0-9]", "", domain_base.lower())
            if not domain_norm:
                continue
            if int(fuzz.ratio(name_norm, domain_norm)) >= 60:
                return url
    return ""


_SERPER_ENDPOINT = "https://google.serper.dev/search"

_COUNTRY_NAMES_MAP: dict[str, str] = {
    "BF": "Burkina Faso", "CI": "Côte d'Ivoire", "SN": "Sénégal",
    "GH": "Ghana", "NG": "Nigeria", "ML": "Mali", "NE": "Niger",
    "GN": "Guinée", "TG": "Togo", "CM": "Cameroun", "KE": "Kenya",
    "MA": "Maroc", "BJ": "Bénin", "CD": "Congo RDC", "MG": "Madagascar",
}

_SKIP_DOMAINS_SERPER = {
    "facebook", "linkedin", "instagram", "twitter", "youtube",
    "google", "wikipedia", "goafricaonline", "kinamap", "elidge",
    "afrikta", "africa-business",
}


async def _serper_subsidiary_search(
    company_name: str,
    iso2: str,
    parent_domain: str,
) -> str:
    """Fix 15 — Search Serper for a subsidiary URL when a parent company is detected.

    Query: "{company_name}" {country_name} -site:{parent_domain}
    Returns the first non-parent, non-social URL found, or empty string.
    """
    api_key = os.getenv("SERPER_API_KEY", "")
    if not api_key:
        return ""

    country_name = _COUNTRY_NAMES_MAP.get(iso2.upper(), iso2)
    query = f'"{company_name}" {country_name} -site:{parent_domain}'

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                _SERPER_ENDPOINT,
                json={"q": query, "gl": iso2.lower(), "hl": "fr", "num": 10},
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("serper_subsidiary_error", company=company_name, error=str(exc))
        return ""

    for item in data.get("organic", []):
        link: str = item.get("link", "")
        if not link:
            continue
        try:
            netloc = _strip_www(urlparse(link).netloc)
        except Exception:
            continue
        if not netloc or parent_domain in netloc:
            continue
        if any(s in netloc for s in _SKIP_DOMAINS_SERPER):
            continue
        return link

    return ""


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

    # Fix 6: Collision risk pre-scoring
    collision_risk, collision_penalty = compute_collision_risk(company.importer_name)

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

    sources = _build_sources(config, iso2)
    all_source_results: list[SourceResult] = []
    all_audit_entries: list[AuditEntry] = []
    collected_phones: list[PhoneResult] = []
    collected_emails: list[EmailResult] = []
    discovered_website: str | None = None
    discovered_domain: str | None = None
    source_notes: list[str] = []
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
        if result.notes:
            source_notes.append(result.notes)

        if result.website and not discovered_website:
            discovered_website = result.website
            from .extractors.website import extract_domain
            discovered_domain = extract_domain(result.website)

        # stop_early disabled: all sources always run so every source contributes
        # and cross-source confidence bonuses are correctly accumulated

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

    # --- Fix 1: Email domain verification (HTTP) ---

    domain_notes: list[str] = []
    email_penalty = 0
    verified_emails: list[EmailResult] = []

    async def _verify_all(emails: list[EmailResult]) -> list[DomainVerificationResult]:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 AfriEnrich/1.0"}, timeout=12
        ) as cl:
            tasks = [
                verify_email_domain(e.address, company.importer_name, iso2, cl)
                for e in emails
            ]
            return list(await asyncio.gather(*tasks))

    domain_results = await _verify_all(unique_emails)

    verified_email_statuses: dict[str, str] = {}

    for email, dv in zip(unique_emails, domain_results, strict=True):
        if dv.status == "rejected":
            email_filter_notes.append(
                f"Email rejected ({email.address}): {dv.rejection_reason}"
            )
            email_penalty += abs(dv.confidence_delta)
        else:
            verified_emails.append(email)
            verified_email_statuses[email.address] = dv.status
            if dv.status == "unverified":
                email_penalty += abs(dv.confidence_delta)
                domain_notes.append(
                    f"Domain unverified ({email.address}): {dv.rejection_reason}"
                )
            elif dv.status == "accepted" and dv.confidence_delta > 0:
                domain_notes.append(f"Domain verified (BF signals) ({email.address})")

    # Fix 17: strong domain verification waives the collision risk penalty
    domain_verified = any(v == "accepted" for v in verified_email_statuses.values())

    # Soft domain-match warnings — skip for fully-verified emails (Fix 1 already confirmed them)
    for email in verified_emails:
        if verified_email_statuses.get(email.address) == "accepted":
            continue  # domain verifier already confirmed company + country match
        pen, d_note = validate_email_domain_match(email, company.importer_name, iso2)
        if d_note:
            domain_notes.append(d_note)
            email_penalty += pen

    final_emails = verified_emails

    # --- Fix 3: Recruitment / role email flagging ---
    role_notes: list[str] = []
    role_penalty = 0
    all_recruitment = bool(final_emails)  # starts True, flipped if any non-recruitment found
    for email in final_emails:
        role_tag, r_pen, r_note = check_email_role(email)
        if role_tag:
            role_penalty += r_pen
            role_notes.append(r_note)
            if role_tag != "recruitment":
                all_recruitment = False
        else:
            all_recruitment = False

    # --- Website URL validation: category page rejection + country-path check (Fix 4/10) ---
    website_penalty, website_note = validate_website_url(
        discovered_website or "", iso2, company.importer_name
    )
    _strip_website = website_note in (
        "directory_category_page_not_company_page",
        "directory_listing_wrong_company",
    )
    if _strip_website and discovered_website:
        # Directory page for wrong category/company — strip it
        log.debug("website_directory_rejected", url=discovered_website, reason=website_note)
        discovered_website = None

    # --- Fix 5/15: Parent/subsidiary detection (URL-pattern, no HTTP) ---
    parent_penalty = 0
    parent_note = ""
    if discovered_website:
        parent_penalty, parent_note = verify_website_country(
            discovered_website, company.importer_name, iso2
        )
        if parent_note == "possible_parent_company":
            log.debug("parent_company_detected", url=discovered_website)

    # --- Fix 15: Subsidiary domain email extraction (only when parent company detected) ---
    # URL-pattern parent detection (Fix 5/15) triggers the subsidiary search.
    if parent_note == "possible_parent_company":
        from .extractors.email import extract_emails_raw as _raw_extract
        _parent_netloc = _strip_www(urlparse(discovered_website or "").netloc)

        # First: look for a subsidiary URL among already-collected candidate_urls
        _sub_url = (
            _find_subsidiary_url(all_source_results, company.importer_name, _parent_netloc)
            if all_source_results
            else ""
        )

        # Fallback: Serper search for subsidiary when none found in existing results
        if not _sub_url:
            _sub_url = await _serper_subsidiary_search(
                company.importer_name, iso2, _parent_netloc
            )

        if _sub_url:
            log.debug("subsidiary_found", url=_sub_url, company=company.importer_name)
            try:
                async with httpx.AsyncClient(
                    headers={"User-Agent": "Mozilla/5.0 AfriEnrich/1.0"}, timeout=12
                ) as _cl:
                    _resp = await _cl.get(_sub_url, follow_redirects=True)
                    if _resp.status_code < 400:
                        _sub_domain = _strip_www(urlparse(_sub_url).netloc)
                        _sub_emails = _raw_extract(
                            _resp.text[:15000], _sub_domain, "subsidiary_domain", 70
                        )
                        final_emails = [*final_emails, *_sub_emails]
                        source_notes.append(f"subsidiary_domain_crawled={_sub_url}")
                        parent_note = f"possible_parent_company; subsidiary={_sub_url}"
            except Exception as exc:
                log.warning("subsidiary_fetch_error", url=_sub_url, error=str(exc))

    # Duplicate phone detection across run
    for p in final_phones:
        duplicate_detector.record(p.number, company.row_index)

    # Fix 10: -15 penalty when a directory listing matched only one of slug/title
    directory_penalty = 15 if "directory_slug_ambiguous_match" in source_notes else 0

    # Fix 17: waive collision penalty when domain verification confirmed the company/country
    effective_collision_penalty = 0 if domain_verified else collision_penalty

    # Confidence scoring
    overall_confidence = compute_overall_confidence(
        all_source_results,
        company,
        final_phones,
        final_emails,
        found_name=normalized,
    )
    # Apply quality penalties (domain mismatch, website country, recruitment, parent, collision)
    overall_confidence = max(
        0,
        overall_confidence
        - email_penalty
        - website_penalty
        - role_penalty
        - parent_penalty
        - effective_collision_penalty
        - directory_penalty,
    )
    # Fix 6: cap confidence at 75 for high-collision-risk companies (waived when domain verified)
    if collision_risk == "high" and not domain_verified:
        overall_confidence = min(overall_confidence, 75)

    # Determine search_status (Fix 2 — confidence-gated thresholds)
    has_phone = bool(final_phones)
    has_email = bool(final_emails)
    has_website = bool(discovered_website)
    has_any = has_phone or has_email or has_website

    if not has_any:
        search_status = "not_found"
    elif overall_confidence < 35:
        search_status = "low_confidence"
    elif all_recruitment and final_emails:
        # Fix 3: only recruitment emails found — cap at partial regardless of confidence
        search_status = "partial"
    elif overall_confidence >= 65 and has_phone and (has_email or has_website):
        search_status = "enriched"
    elif overall_confidence >= 50 and has_any:
        search_status = "partial"
    else:
        # confidence 35-49 or data found but signals conflict
        search_status = "ambiguous"

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
        phone_6=_phone_field(final_phones, 5, "number"),
        phone_6_source=_phone_field(final_phones, 5, "source"),
        phone_6_confidence=_phone_int(final_phones, 5, "confidence"),
        phone_6_type=_phone_field(final_phones, 5, "phone_type"),
        phone_6_audit_id=_phone_field(final_phones, 5, "audit_id"),
        phone_7=_phone_field(final_phones, 6, "number"),
        phone_7_source=_phone_field(final_phones, 6, "source"),
        phone_7_confidence=_phone_int(final_phones, 6, "confidence"),
        phone_7_type=_phone_field(final_phones, 6, "phone_type"),
        phone_7_audit_id=_phone_field(final_phones, 6, "audit_id"),
        phone_8=_phone_field(final_phones, 7, "number"),
        phone_8_source=_phone_field(final_phones, 7, "source"),
        phone_8_confidence=_phone_int(final_phones, 7, "confidence"),
        phone_8_type=_phone_field(final_phones, 7, "phone_type"),
        phone_8_audit_id=_phone_field(final_phones, 7, "audit_id"),
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
        email_3=_email_field(final_emails, 2, "address"),
        email_3_source=_email_field(final_emails, 2, "source"),
        email_3_confidence=_email_int(final_emails, 2, "confidence"),
        email_3_validation=_email_field(final_emails, 2, "validation_level"),
        email_3_audit_id=_email_field(final_emails, 2, "audit_id"),
        email_4=_email_field(final_emails, 3, "address"),
        email_4_source=_email_field(final_emails, 3, "source"),
        email_4_confidence=_email_int(final_emails, 3, "confidence"),
        email_4_validation=_email_field(final_emails, 3, "validation_level"),
        email_4_audit_id=_email_field(final_emails, 3, "audit_id"),
        email_5=_email_field(final_emails, 4, "address"),
        email_5_source=_email_field(final_emails, 4, "source"),
        email_5_confidence=_email_int(final_emails, 4, "confidence"),
        email_5_validation=_email_field(final_emails, 4, "validation_level"),
        email_5_audit_id=_email_field(final_emails, 4, "audit_id"),
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
            role_notes,
            [parent_note] if parent_note else [],
            ["High name collision risk — verify manually"] if collision_risk == "high" else [],
            source_notes,
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
    role_notes: list[str] | None = None,
    parent_notes: list[str] | None = None,
    collision_notes: list[str] | None = None,
    source_notes: list[str] | None = None,
) -> str:
    parts: list[str] = []
    if ai_cost:
        parts.append(f"ai_cost=${ai_cost:.5f}")
    parts.extend(phone_notes)
    parts.extend(email_notes)
    parts.extend(domain_notes)
    if website_note:
        parts.append(website_note)
    if role_notes:
        parts.extend(role_notes)
    if parent_notes:
        parts.extend(parent_notes)
    if collision_notes:
        parts.extend(collision_notes)
    if source_notes:
        parts.extend(source_notes)
    return "; ".join(parts)
