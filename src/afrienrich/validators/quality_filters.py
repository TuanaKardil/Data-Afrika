"""Pre-save quality validators for phones, emails, and websites.

Five named functions (validate_phone_prefix, validate_email_format,
validate_email_uniqueness, validate_website_country, validate_email_domain_match)
plus QualityFilter — a stateful wrapper that holds per-run seen-email state.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import structlog
import yaml
from rapidfuzz import fuzz

from ..models import EmailResult, PhoneResult

log = structlog.get_logger()

_COUNTRIES_PATH = Path(__file__).parent.parent.parent.parent / "config" / "countries.yaml"

# ── Phone prefix maps ────────────────────────────────────────────────────────

_PHONE_PREFIX_FALLBACK: dict[str, list[str]] = {
    "BF": ["226"], "CI": ["225"], "SN": ["221"], "TG": ["228"],
    "GH": ["233"], "ML": ["223"], "NE": ["227"], "GN": ["224"],
    "MA": ["212"], "NG": ["234"], "KE": ["254"], "CM": ["237"],
    "CD": ["243"], "ET": ["251"], "TZ": ["255"], "EG": ["20"],
    "ZA": ["27"],
}

_PHONE_PREFIXES: dict[str, list[str]] = {}  # populated on first call


def _get_phone_prefixes() -> dict[str, list[str]]:
    global _PHONE_PREFIXES
    if _PHONE_PREFIXES:
        return _PHONE_PREFIXES
    try:
        with open(_COUNTRIES_PATH) as f:
            data: dict[str, object] = yaml.safe_load(f) or {}
        result: dict[str, list[str]] = {}
        for iso2, cfg in data.items():
            if isinstance(cfg, dict) and "phone_code" in cfg:
                result[str(iso2)] = [str(cfg["phone_code"])]
        _PHONE_PREFIXES = result if result else _PHONE_PREFIX_FALLBACK
    except Exception:
        _PHONE_PREFIXES = _PHONE_PREFIX_FALLBACK
    return _PHONE_PREFIXES


# ── Email format ─────────────────────────────────────────────────────────────

_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
# Bogus TLDs: .coms, .orgg, .nett, or anything unusually long
_BAD_TLD_RE = re.compile(r"\.(com[a-z]+|org[a-z]+|net[a-z]+|[a-z]{7,})$", re.IGNORECASE)
_GENERIC_EMAIL_DOMAINS = {
    "gmail", "yahoo", "hotmail", "outlook", "icloud", "live", "protonmail",
}

# ── Recruitment / role email detection (Fix 3) ───────────────────────────────

_RECRUITMENT_KEYWORDS = {
    "recrutement", "recruitment", "rh", "hr", "careers", "career",
    "emploi", "jobs", "job", "talent", "hiring", "embauche",
}
_SUPPORT_KEYWORDS = {"support", "sav", "service-client", "helpdesk"}

# ── Fix 6: Collision risk scoring ────────────────────────────────────────────

_GENERIC_SECTOR_WORDS = {
    "metal", "construction", "materiaux", "matériaux", "bois", "acier",
    "steel", "bati", "build", "general", "général", "groupe", "group",
    "commerce", "trading", "import", "export", "distribution", "service",
    "services", "industrie", "industry",
}

# Known international brands that exist in multiple countries
_KNOWN_BRANDS = {
    "iamgold", "shell", "total", "bolloré", "bollore", "orange", "mtn",
    "airtel", "nestle", "unilever", "cargill", "olam", "sifca", "societe generale",
}

_LEGAL_SUFFIX_STRIP = re.compile(
    r"\b(sarl|sa|sas|ltd|limited|gie|snc|suarl|plc|eirl|srl|spa|lda|pte|inc|corp)\b",
    re.IGNORECASE,
)


def compute_collision_risk(company_name: str) -> tuple[str, int]:
    """Return (risk_level, penalty). risk_level is 'high' or 'normal'. (Fix 6)"""
    stripped = _LEGAL_SUFFIX_STRIP.sub("", company_name).strip()
    words = [w for w in re.split(r"[\s\-_&'+.,/]", stripped.upper()) if w]

    score = 0

    # Short name (≤10 chars after stripping suffixes and spaces)
    if len(stripped.replace(" ", "")) <= 10:
        score += 2

    # Any word in the name that is a 2–5 char all-caps acronym (e.g. "GMC", "UNI")
    if any(
        2 <= len(w) <= 5 and w.isalpha() and w.upper() == w
        for w in words
    ):
        score += 3

    # Name ends with a short acronym (trade name), e.g. "… CONSTRUCTION GMC"
    last_word = words[-1] if words else ""
    if 2 <= len(last_word) <= 5 and last_word.isalpha() and last_word.upper() == last_word:
        score += 2

    # All meaningful words are generic sector words
    meaningful = [
        w for w in words
        if re.match(r"^[A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ]{2,}$", w)
    ]
    if meaningful and all(
        re.sub(r"[^a-z]", "", w.lower()) in _GENERIC_SECTOR_WORDS for w in meaningful
    ):
        score += 2

    # Known international brand
    name_lower = company_name.lower()
    if any(brand in name_lower for brand in _KNOWN_BRANDS):
        score += 3

    if score >= 4:
        return "high", 10
    return "normal", 0


# ── Website country paths ─────────────────────────────────────────────────────

_DIRECTORY_COUNTRY_PATH: dict[str, re.Pattern[str]] = {
    "goafricaonline.com": re.compile(r"/([a-z]{2})/"),
    "africa-business.com": re.compile(r"/([a-z]{2})/"),
}

# ── Email TLD → ISO2 (ccTLDs only) ───────────────────────────────────────────

_CCTLD_COUNTRY: dict[str, str] = {
    ".tn": "TN", ".ma": "MA", ".sn": "SN", ".ci": "CI", ".bf": "BF",
    ".gh": "GH", ".ng": "NG", ".cm": "CM", ".tg": "TG", ".ml": "ML",
    ".ne": "NE", ".gn": "GN", ".ke": "KE", ".za": "ZA", ".eg": "EG",
    ".dz": "DZ", ".cn": "CN", ".fr": "FR", ".de": "DE", ".es": "ES",
    ".tr": "TR", ".ru": "RU", ".uk": "GB", ".in": "IN",
}

# ── Domain–company matching ───────────────────────────────────────────────────

_LEGAL_STOP_WORDS = {
    "SARL", "SA", "SAS", "LTD", "LIMITED", "GIE", "SNC", "SUARL", "PLC",
    "EIRL", "SRL", "CO", "AND", "DE", "DU", "DES", "ET", "LA", "LE",
    "LES", "THE", "OF", "AU", "AUX", "EN", "INT",
}

# Country-name keywords in an email address that signal a foreign-country conflict
_COUNTRY_WORDS: dict[str, list[str]] = {
    "SN": ["senegal"], "CI": ["cotedivoire", "coted", "civ"],
    "GH": ["ghana"], "NG": ["nigeria", "naija"], "KE": ["kenya"],
    "TG": ["togo"], "ML": ["mali"], "NE": ["niger"],
    "GN": ["guinea", "guinee"], "MA": ["maroc", "morocco"],
    "CM": ["cameroun", "cameroon"], "BF": ["burkina"],
}


def _normalize(text: str) -> str:
    """Lowercase + strip non-alphanumeric."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _norm_slug(text: str) -> str:
    """Lowercase, strip accents, keep alphanumeric and spaces (for token-based fuzzy match)."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9 ]", " ", stripped).strip()


def _build_abbreviation(company_name: str) -> str:
    """Build initialism from meaningful words: 'GMC' from 'Général Matériaux Construction GMC'."""
    words = re.split(r"[\s\-_&'+.,/]", company_name.upper())
    meaningful = [
        w for w in words
        if w and w not in _LEGAL_STOP_WORDS and re.match(r"^[A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ]{2,}$", w)
    ]
    if len(meaningful) >= 2:
        return "".join(w[0] for w in meaningful)
    return ""


def _company_matches_domain(company_name: str, domain: str) -> bool:
    """Return True if the email domain is plausibly this company's own domain."""
    domain_clean = _normalize(domain)
    company_clean = _normalize(company_name)

    # 1. Fuzzy similarity ≥ 60%
    if fuzz.partial_ratio(company_clean, domain_clean) >= 60:
        return True

    # 2. Any explicit meaningful word (≥3 chars) from company appears in domain
    words = re.split(r"[\s\-_&'+.,/]", company_name)
    meaningful = [
        _normalize(w) for w in words
        if w and w.upper() not in _LEGAL_STOP_WORDS and len(w) >= 3
    ]
    if meaningful and any(w in domain_clean for w in meaningful):
        return True

    # 3. Abbreviation (≥2 chars) appears in domain
    abbr = _build_abbreviation(company_name).lower()
    return bool(len(abbr) >= 2 and abbr in domain_clean)


def _tld_country(domain: str) -> str | None:
    """Return ISO2 if the domain's TLD is a known ccTLD, else None."""
    parts = domain.lower().rsplit(".", 1)
    if len(parts) == 2:
        return _CCTLD_COUNTRY.get("." + parts[1])
    return None


# ── Stateless validators ─────────────────────────────────────────────────────

def check_email_role(email: EmailResult) -> tuple[str, int, str]:
    """Return (role_tag, penalty, note) for recruitment/support emails (Fix 3)."""
    addr = email.address.lower().strip()
    local = addr.split("@")[0] if "@" in addr else addr
    # Split on common separators to catch "essakane_recrutement" → ["essakane", "recrutement"]
    parts = set(re.split(r"[_.\-+]", local)) | {local}
    for part in parts:
        if part in _RECRUITMENT_KEYWORDS:
            return "recruitment", 25, "Recruitment email, not suitable for B2B outreach"
        if part in _SUPPORT_KEYWORDS:
            return "support", 10, "Support email, low-value for B2B outreach"
    return "", 0, ""


def validate_phone_prefix(phone: PhoneResult, iso2: str) -> tuple[bool, str]:
    """Return (True, "") if the phone matches the expected country dial code."""
    prefixes = _get_phone_prefixes().get(iso2) or _PHONE_PREFIX_FALLBACK.get(iso2)
    if not prefixes:
        return True, ""
    digits = phone.number.lstrip("+")
    for prefix in prefixes:
        if digits.startswith(prefix):
            return True, ""
    actual = digits[:3] if len(digits) >= 3 else digits
    return False, f"Filtered: foreign prefix (+{actual})"


def validate_email_format(email: EmailResult) -> tuple[bool, str]:
    """Return (True, "") if the address passes format and TLD checks."""
    addr = email.address.strip()
    if not _EMAIL_REGEX.match(addr):
        return False, f"Invalid email format: {addr}"
    domain_part = addr.split("@")[-1]
    if _BAD_TLD_RE.search(domain_part):
        return False, f"Invalid email format (bad TLD): {addr}"
    return True, ""


def validate_email_uniqueness(email: EmailResult, seen_emails: set[str]) -> tuple[bool, str]:
    """Return (True, "") if this email has not already been assigned to another company."""
    addr = email.address.lower().strip()
    if addr in seen_emails:
        return False, f"Skipped: email already mapped to another company ({addr})"
    return True, ""


_GAO_CATEGORY_RE = re.compile(r"goafricaonline\.com/[a-z]{2}/annuaire/")
_GAO_COMPANY_RE = re.compile(r"goafricaonline\.com/[a-z]{2}/\d+")
# Fix 10: match the slug portion of a GoAfrica listing URL: /bf/17706-gmf-materiels-...
_GAO_LISTING_RE = re.compile(r"goafricaonline\.com/[a-z]{2}/(\d+-[a-z0-9-]+)")

_SLUG_THRESHOLD = 70


def _gao_listing_slug_words(url: str) -> str:
    """Extract human-readable words from a GoAfrica listing URL slug."""
    m = _GAO_LISTING_RE.search(url.lower())
    if not m:
        return ""
    slug = m.group(1)
    slug = re.sub(r"^\d+-", "", slug)  # strip leading '123-'
    # Drop location suffixes that pollute matching (city / country tokens at the end)
    slug = re.sub(r"-(ouagadougou|abidjan|dakar|accra|lagos|nairobi|cotonou|lome|bamako|niamey"
                  r"|conakry|yaounde|burkina|faso|senegal|ghana|nigeria|kenya|togo|mali|niger"
                  r"|guinee|cameroun|benin|madagascar).*$", "", slug)
    return slug.replace("-", " ").strip()


def validate_website_url(
    url: str, iso2: str, company_name: str = ""
) -> tuple[int, str]:
    """Return (penalty, note). Non-zero for category pages or wrong-country directory paths."""
    if not url:
        return 0, ""

    # Fix 4 / Fix 7: GoAfrica Online category page rejection (-20)
    if _GAO_CATEGORY_RE.search(url):
        return 20, "directory_category_page_not_company_page"

    # Fix 10: GoAfrica listing URL slug vs company name — reject if slug score < 70
    if company_name and _GAO_LISTING_RE.search(url):
        slug_words = _gao_listing_slug_words(url)
        if slug_words:
            score = int(fuzz.token_set_ratio(
                _norm_slug(company_name), _norm_slug(slug_words)
            ))
            if score < _SLUG_THRESHOLD:
                log.debug(
                    "gao_listing_slug_mismatch",
                    url=url,
                    company=company_name,
                    slug=slug_words,
                    score=score,
                )
                return 20, "directory_listing_wrong_company"

    for domain_pattern, path_re in _DIRECTORY_COUNTRY_PATH.items():
        if domain_pattern not in url:
            continue
        m = path_re.search(url)
        if m:
            url_iso2 = m.group(1).upper()
            if url_iso2 != iso2.upper():
                return 20, f"Suspicious URL: country mismatch ({url_iso2} != {iso2})"
    return 0, ""


def validate_website_country(
    url: str, iso2: str, company_name: str = ""
) -> tuple[int, str]:
    """Alias kept for callers that have not yet migrated to validate_website_url."""
    return validate_website_url(url, iso2, company_name)


def validate_email_domain_match(
    email: EmailResult,
    company_name: str,
    iso2: str,
) -> tuple[int, str]:
    """Return (penalty, note) for domain–company mismatch and foreign-country signals."""
    addr = email.address.lower().strip()
    domain = addr.split("@")[-1] if "@" in addr else ""
    notes: list[str] = []
    penalty = 0

    is_generic = any(g in domain for g in _GENERIC_EMAIL_DOMAINS)

    # For free/generic providers, skip domain and TLD checks — they don't indicate country
    if not is_generic:
        # Rule: domain must plausibly belong to the company
        if not _company_matches_domain(company_name, domain):
            notes.append("Warning: email domain does not match company")
            penalty += 15

        # Rule: email TLD must not conflict with company country
        tld_iso = _tld_country(domain)
        if tld_iso and tld_iso != iso2.upper():
            tld_str = domain.rsplit(".", 1)[-1]
            notes.append(f"Warning: email TLD country mismatch (.{tld_str} is {tld_iso})")
            penalty += 15

    # Rule: foreign country name in the email address
    for country_iso, words in _COUNTRY_WORDS.items():
        if country_iso == iso2:
            continue
        for word in words:
            if word in addr:
                notes.append(f"Warning: foreign country reference in email ({word})")
                penalty += 10
                break

    return penalty, "; ".join(notes)


# ── Stateful wrapper ─────────────────────────────────────────────────────────

class QualityFilter:
    """Holds per-run seen-email state and orchestrates all quality checks."""

    def __init__(self) -> None:
        self._seen_emails: set[str] = set()

    def filter_phones(
        self, phones: list[PhoneResult], iso2: str
    ) -> tuple[list[PhoneResult], list[str]]:
        accepted: list[PhoneResult] = []
        notes: list[str] = []
        for phone in phones:
            ok, reason = validate_phone_prefix(phone, iso2)
            if ok:
                accepted.append(phone)
            else:
                notes.append(reason)
                log.debug("phone_filtered", number=phone.number, reason=reason, iso2=iso2)
        return accepted, notes

    def check_email_uniqueness(self, email: EmailResult) -> tuple[bool, str]:
        return validate_email_uniqueness(email, self._seen_emails)

    def register_emails(self, emails: list[EmailResult]) -> None:
        for e in emails:
            self._seen_emails.add(e.address.lower().strip())
