"""Fix 1 — Email domain verification via HTTP.

For every candidate email, verifies the domain belongs to the target company
and country before accepting it. Free providers are exempt from HTTP checks.
"""

from __future__ import annotations

import re

import httpx
import structlog
from rapidfuzz import fuzz

log = structlog.get_logger()

# Country-specific positive signals for BF (and other countries)
_COUNTRY_SIGNALS: dict[str, list[str]] = {
    "BF": [
        "burkina", "ouagadougou", "ouaga", "bobo-dioulasso", "bobo",
        "+226", "226 ", " 226", "(226)", "00226", ".bf",
        "rccm bf", "ifu bf", "ifu-bf", "burkinabè", "burkinabe",
    ],
    "CI": ["côte d'ivoire", "cote d'ivoire", "abidjan", "+225", "225 ", ".ci"],
    "SN": ["sénégal", "senegal", "dakar", "+221", "221 ", ".sn"],
    "GH": ["ghana", "accra", "+233", "233 ", ".gh"],
    "NG": ["nigeria", "lagos", "abuja", "+234", "234 ", ".ng"],
    "TG": ["togo", "lomé", "lome", "+228", "228 ", ".tg"],
    "ML": ["mali", "bamako", "+223", "223 ", ".ml"],
    "NE": ["niger", "niamey", "+227", "227 ", ".ne"],
    "GN": ["guinée", "guinee", "conakry", "+224", "224 ", ".gn"],
    "MA": ["maroc", "morocco", "casablanca", "rabat", "+212", "212 ", ".ma"],
    "CM": ["cameroun", "cameroon", "yaoundé", "douala", "+237", "237 ", ".cm"],
    "KE": ["kenya", "nairobi", "+254", "254 ", ".ke"],
}

# ccTLDs that indicate a specific non-target country
_FOREIGN_CCTLDS: dict[str, str] = {
    ".be": "BE", ".fr": "FR", ".tn": "TN", ".ci": "CI", ".sn": "SN",
    ".ng": "NG", ".ma": "MA", ".eg": "EG", ".za": "ZA", ".gh": "GH",
    ".cm": "CM", ".de": "DE", ".uk": "GB", ".us": "US", ".ca": "CA",
    ".it": "IT", ".es": "ES", ".pt": "PT", ".cn": "CN", ".tr": "TR",
    ".ru": "RU", ".in": "IN",
}

# Generic TLDs that don't indicate any country — must be verified via HTTP
_GENERIC_TLDS = {".com", ".org", ".net", ".biz", ".co", ".africa", ".info", ".io"}

# Free email providers — never verify by visiting domain
_FREE_PROVIDERS = {
    "gmail.com", "yahoo.com", "yahoo.fr", "hotmail.com", "outlook.com",
    "orange.fr", "live.com", "hotmail.fr", "icloud.com", "protonmail.com",
    "gmx.com", "zoho.com", "aol.com",
}

_CONTACT_PATHS = ["/", "/contact", "/contactez-nous", "/about", "/a-propos"]


def _extract_domain(email: str) -> str:
    return email.split("@")[-1].lower().strip() if "@" in email else ""


def _get_tld(domain: str) -> str:
    parts = domain.rsplit(".", 1)
    return "." + parts[1] if len(parts) == 2 else ""


def _is_foreign_cctld(domain: str, iso2: str) -> tuple[bool, str]:
    """Return (is_foreign, tld_string). True when TLD is a non-target-country ccTLD."""
    tld = _get_tld(domain)
    country = _FOREIGN_CCTLDS.get(tld)
    if country and country != iso2.upper():
        return True, tld
    return False, ""


def _count_country_signals(text: str, iso2: str) -> int:
    text_lower = text.lower()
    signals = _COUNTRY_SIGNALS.get(iso2.upper(), [])
    return sum(1 for s in signals if s in text_lower)


def _domain_matches_company(company_name: str, domain: str) -> bool:
    """True if the domain URL (without TLD) substantially matches the company name.

    Uses full-string ratio (not partial) to avoid false positives where a short
    company name appears as a substring in an unrelated foreign domain.
    """
    base = re.sub(r"\.[^.]+$", "", domain)  # strip TLD
    base_norm = re.sub(r"[^a-z0-9]", "", base.lower())
    # Strip legal suffixes and country tokens from company name for comparison
    company_clean = re.sub(
        r"\b(sarl|sa|sas|ltd|gie|snc|suarl|plc|burkina|faso|senegal|nigeria|ghana|togo|mali)\b",
        "", company_name.lower(), flags=re.IGNORECASE,
    )
    company_norm = re.sub(r"[^a-z0-9]", "", company_clean)
    if not company_norm or not base_norm:
        return False
    # Full-string ratio: the domain must substantially BE the company name, not just contain it
    return fuzz.ratio(company_norm, base_norm) >= 75


async def _fetch_page(client: httpx.AsyncClient, domain: str) -> tuple[str, bool]:
    """Try contact/about pages and return (page_text, success)."""
    for path in _CONTACT_PATHS:
        for scheme in ("https", "http"):
            url = f"{scheme}://{domain}{path}"
            try:
                resp = await client.get(url, timeout=10, follow_redirects=True)
                if resp.status_code < 400:
                    return resp.text[:15000], True
            except Exception:
                continue
    return "", False


class DomainVerificationResult:
    __slots__ = ("status", "confidence_delta", "rejection_reason")

    def __init__(self, status: str, confidence_delta: int, rejection_reason: str = "") -> None:
        self.status = status  # "accepted", "rejected", "unverified", "free_provider"
        self.confidence_delta = confidence_delta
        self.rejection_reason = rejection_reason


async def verify_email_domain(
    email_address: str,
    company_name: str,
    iso2: str,
    client: httpx.AsyncClient | None = None,
) -> DomainVerificationResult:
    """Verify that the email domain belongs to the target company and country.

    Returns a DomainVerificationResult with status and confidence_delta to apply.
    """
    domain = _extract_domain(email_address)
    if not domain:
        return DomainVerificationResult("rejected", -15, "no_domain")

    # Free providers: accept as-is, confidence capped at 55 (no delta applied here;
    # caller must cap confidence)
    if domain in _FREE_PROVIDERS:
        return DomainVerificationResult("free_provider", 0)

    # Step 1: TLD rejection (fast, no HTTP)
    is_foreign, tld = _is_foreign_cctld(domain, iso2)
    if is_foreign:
        # But allow if TLD matches target country (e.g. .bf for BF)
        tld_country_match = _FOREIGN_CCTLDS.get(tld) == iso2.upper()
        if not tld_country_match:
            return DomainVerificationResult(
                "rejected", -15, f"foreign_country_tld ({tld})"
            )

    # Step 2: Fetch and verify domain (generic TLDs and target-country ccTLD all require this)
    own_client = client is None
    active_client: httpx.AsyncClient = (
        client if client is not None
        else httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 AfriEnrich/1.0"})
    )

    try:
        page_text, fetched = await _fetch_page(active_client, domain)
    finally:
        if own_client:
            await active_client.aclose()

    if not fetched:
        # Fetch failed: unverified, confidence capped at 45
        return DomainVerificationResult("unverified", -10, "domain_fetch_failed")

    # Step 3: Country signal check
    signals_found = _count_country_signals(page_text, iso2)

    # Override: if the domain URL itself is a strong match for the company name (≥80%),
    # the company clearly owns this domain — accept even without explicit country signals.
    domain_is_own = _domain_matches_company(company_name, domain)

    if signals_found == 0 and not domain_is_own:
        return DomainVerificationResult(
            "rejected", -20, "no_country_signals_on_domain"
        )

    # Step 4: Company name fuzzy match on page text
    snippet = page_text[:2000]
    fuzzy_score = fuzz.partial_ratio(company_name.lower(), snippet.lower())
    if fuzzy_score < 60 and not domain_is_own:
        return DomainVerificationResult(
            "rejected", -20, f"company_name_not_found_on_domain (score={fuzzy_score})"
        )

    # All checks passed
    return DomainVerificationResult("accepted", +10)


# ── Fix 15: URL-pattern-based parent detection (no HTTP) ─────────────────────

# Words that disqualify a token from being the "primary" company identifier
_PARENT_SKIP_WORDS = frozenset({
    "SARL", "SA", "SAS", "LTD", "LIMITED", "GIE", "SNC", "SUARL", "PLC",
    "EIRL", "SRL", "INC", "CORP", "CO",
    # Generic sector words that appear in many company names
    "METAL", "STEEL", "ACIER", "BOIS", "WOOD", "GLASS", "VERRE",
    "TRADE", "GROUP", "GROUPE", "HOLDING",
    # Country / location tokens
    "BURKINA", "FASO", "SENEGAL", "NIGERIA", "GHANA", "TOGO", "MALI",
    "NIGER", "GUINEE", "GUINEA", "CAMEROUN", "CAMEROON", "KENYA",
    "AFRIQUE", "AFRICA", "OUEST", "WEST",
    # Ultra-generic business words
    "DISTRIBUTION", "SERVICES", "SERVICE", "COMMERCE", "TRADING",
    "IMPORT", "EXPORT", "INTERNATIONAL", "INDUSTRIE", "INDUSTRY",
    "CONSTRUCTION", "MATERIAUX", "MATERIAL", "GENERAL", "GENERALE",
})

_MIN_PRIMARY_TOKEN_LEN = 5


def _primary_company_token(company_name: str) -> str:
    """Return the first significant word (≥5 chars, not generic/legal) from the company name."""
    clean = re.sub(r"[^a-zA-Z0-9\s]", " ", company_name)
    for word in clean.upper().split():
        if len(word) >= _MIN_PRIMARY_TOKEN_LEN and word not in _PARENT_SKIP_WORDS:
            return word.lower()
    return ""


def _all_significant_tokens(company_name: str) -> list[str]:
    """Return all significant words (≥5 chars, not generic/legal) from the company name."""
    clean = re.sub(r"[^a-zA-Z0-9\s]", " ", company_name)
    return [
        w.lower()
        for w in clean.upper().split()
        if len(w) >= _MIN_PRIMARY_TOKEN_LEN and w not in _PARENT_SKIP_WORDS
    ]


def verify_website_country(
    url: str,
    company_name: str,
    iso2: str,
) -> tuple[int, str]:
    """Fix 5/15 — URL-pattern-only parent company detection. No HTTP call.

    Returns (penalty, note). Conditions for parent detection:
    1. Country ISO2 code is NOT present in the domain.
    2. Company's primary identifier token IS present in the domain.
    3. Guard: if ≥2 significant company tokens are all present in the domain,
       it is the company's own domain (e.g. iamgoldessakane.com for
       IAMGOLD ESSAKANE SA) — not a parent.
    """
    if not url:
        return 0, ""

    from urllib.parse import urlparse
    parsed = urlparse(url)
    netloc = parsed.netloc
    domain = (netloc[4:] if netloc.startswith("www.") else netloc).lower()
    if not domain:
        return 0, ""

    # Condition 1: country code not embedded in domain
    if iso2.lower() in domain:
        return 0, ""

    # Condition 2: primary token present in domain
    token = _primary_company_token(company_name)
    if not token or token not in domain:
        return 0, ""

    # Guard: if multiple significant company tokens all appear in the domain,
    # this is the company's own domain, not a parent company.
    all_tokens = _all_significant_tokens(company_name)
    if len(all_tokens) >= 2 and sum(1 for t in all_tokens if t in domain) >= 2:
        return 0, ""

    log.debug(
        "parent_company_detected_url_pattern",
        url=url,
        company=company_name,
        token=token,
    )
    return 20, "possible_parent_company"
