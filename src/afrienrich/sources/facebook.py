"""Facebook source. Uses Serper.dev to find the company Facebook URL, then fetches
the page for contact extraction. Tier is country-driven: West/Central Africa = 2.

Fix 12: Serper-based search with 5 query variations; concurrent with GoAfrica when tier=2.
Fix 13: Personal profile URLs accepted at threshold >= 55; facebook_type noted.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
import uuid
from pathlib import Path

import httpx
import structlog
import yaml
from rapidfuzz import fuzz
from selectolax.parser import HTMLParser

from ..extractors.email import extract_emails_raw
from ..extractors.phone import extract_phones
from ..extractors.social import extract_social_urls
from ..models import AuditEntry, CompanyQuery, SocialUrls, SourceResult
from ..utils.rate_limiter import get_limiter
from .base import BaseSource

# HTMLParser imported for Facebook page fetching; selectolax is already a dependency

log = structlog.get_logger()

_SERPER_ENDPOINT = "https://google.serper.dev/search"
_COUNTRIES_PATH = Path(__file__).parent.parent.parent.parent / "config" / "countries.yaml"

_COUNTRY_NAMES: dict[str, str] = {
    "BF": "Burkina Faso",
    "CI": "Côte d'Ivoire",
    "SN": "Sénégal",
    "GH": "Ghana",
    "NG": "Nigeria",
    "ML": "Mali",
    "NE": "Niger",
    "GN": "Guinée",
    "TG": "Togo",
    "CM": "Cameroun",
    "KE": "Kenya",
    "MA": "Maroc",
    "BJ": "Bénin",
    "GW": "Guinée-Bissau",
    "MR": "Mauritanie",
    "TD": "Tchad",
    "CF": "République Centrafricaine",
    "CG": "Congo",
    "CD": "Congo RDC",
    "MG": "Madagascar",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# Fix 16: country words to detect in Facebook URL slugs; keyed by the ISO2 that word implies.
# A hit means the slug advertises a different country than the searched company.
_FB_COUNTRY_SLUG_WORDS: dict[str, list[str]] = {
    "FR": ["france", "francais", "french", "paris"],
    "US": ["usa", "america", "american", "unitedstates"],
    "GB": ["uk", "britain", "england", "london"],
    "DE": ["germany", "deutschland", "german"],
    "CN": ["china", "chinese"],
    "SN": ["senegal", "senegalais", "dakar"],
    "CI": ["cotedivoire", "abidjan", "ivoirien"],
    "GH": ["ghana", "ghanaian"],
    "NG": ["nigeria", "lagos", "nigerian"],
    "KE": ["kenya", "nairobi", "kenyan"],
    "MA": ["maroc", "morocco", "casablanca"],
    "TG": ["togo", "lome", "togolais"],
    "ML": ["mali", "bamako", "malien"],
    "CM": ["cameroun", "cameroon"],
    "GN": ["guinee", "guinea"],
    "NE": ["niger", "niamey"],
    "BJ": ["benin", "cotonou"],
    "MG": ["madagascar"],
}


def _facebook_slug_country_conflict(url: str, iso2: str) -> bool:
    """Return True if the Facebook URL slug contains a word that implies a different country."""
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path
    except Exception:
        return False
    # Normalise: strip separators and accents for substring matching
    slug = _strip_accents(path.lower().replace("-", "").replace("_", "").replace("/", ""))

    for country_iso, words in _FB_COUNTRY_SLUG_WORDS.items():
        if country_iso == iso2.upper():
            continue
        for word in words:
            w = word.replace("-", "").replace(" ", "")
            if w in slug:
                return True
    return False


_LEGAL_SUFFIX_RE = re.compile(
    r"\b(sarl|sa|sas|suarl|gie|snc|ltd|limited|plc|eirl"
    r"|ci|bf|sn|tg|ml|ne|gn|cm"
    r"|burkina|faso|senegal|nigeria|ghana|togo|mali|niger|guinee)\b",
    re.IGNORECASE,
)


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _strip_legal_suffix(name: str) -> str:
    cleaned = _LEGAL_SUFFIX_RE.sub("", name).strip()
    return re.sub(r"\s+", " ", cleaned).strip()


def _facebook_queries(name: str, country_name: str) -> list[str]:
    """Generate up to 5 Serper query variations per Fix 12 spec."""
    clean = _strip_accents(name)
    short = _strip_legal_suffix(clean)
    minimal = short.split()[0] if short.split() else clean.split()[0]
    return [
        f'"{name}" site:facebook.com',
        f'"{clean}" site:facebook.com',
        f'"{short}" site:facebook.com',
        f'"{minimal}" "{country_name}" site:facebook.com',
        f'"{short}" "{country_name}" site:facebook.com',
    ]


def _score_facebook_result(link: str, title: str, snippet: str, company_name: str) -> int:
    """Score relevance of a Serper result to the company. Checks title then snippet."""
    name_lower = _strip_accents(company_name).lower()
    # Strip "- Facebook" / "| Facebook" suffix from title before scoring
    title_core = re.sub(
        r"[-|–—]\s*(?:facebook|fb).*$", "", title, flags=re.IGNORECASE
    ).strip()
    title_score = int(fuzz.token_set_ratio(name_lower, title_core.lower()))
    # Snippet: limit to first 300 chars to reduce false positives
    snippet_score = int(fuzz.token_set_ratio(name_lower, snippet[:300].lower())) if snippet else 0
    return max(title_score, snippet_score)


def get_facebook_tier(iso2: str) -> int:
    """Read facebook_tier from countries.yaml for the given ISO2 code."""
    try:
        with open(_COUNTRIES_PATH) as f:
            data = yaml.safe_load(f) or {}
        cfg = data.get(iso2.upper(), {})
        if isinstance(cfg, dict):
            tier_val = cfg.get("facebook_tier")
            if isinstance(tier_val, int):
                return tier_val
    except Exception:
        pass
    return 4


class FacebookSource(BaseSource):
    name = "facebook"
    tier = 4  # overridden per-country via get_facebook_tier() in pipeline._build_sources()
    base_confidence = 55
    rate_limit_per_second = 0.5

    def __init__(self) -> None:
        self._api_key = os.getenv("SERPER_API_KEY", "")

    async def search(self, company: CompanyQuery) -> SourceResult:
        limiter = get_limiter(self.name)
        await limiter.acquire()

        iso2 = company.country
        country_name = _COUNTRY_NAMES.get(iso2.upper(), iso2)
        queries = _facebook_queries(company.importer_name, country_name)

        audit_entries: list[AuditEntry] = []
        found_fb_url = ""
        facebook_type = ""
        all_text = ""

        # --- Fix 12: Try each query until we find a matching Facebook URL ---
        async with httpx.AsyncClient(timeout=15) as serper_client:
            for query in queries:
                audit_id = str(uuid.uuid4())
                t0 = time.monotonic()
                status = "miss"

                try:
                    resp = await serper_client.post(
                        _SERPER_ENDPOINT,
                        json={"q": query, "gl": iso2.lower(), "hl": "fr", "num": 10},
                        headers={
                            "X-API-KEY": self._api_key,
                            "Content-Type": "application/json",
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    for item in data.get("organic", []):
                        link = item.get("link", "")
                        if "facebook.com" not in link.lower():
                            continue

                        title = item.get("title", "")
                        snippet = item.get("snippet", "")
                        score = _score_facebook_result(
                            link, title, snippet, company.importer_name
                        )

                        # Fix 13: personal profiles (no /pages/ or /p/) threshold = 55
                        is_personal = "/pages/" not in link and "/p/" not in link
                        threshold = 55 if is_personal else 65

                        if score >= threshold:
                            # Fix 16: skip if slug advertises a different country
                            if _facebook_slug_country_conflict(link, iso2):
                                log.debug(
                                    "facebook_country_slug_conflict",
                                    url=link,
                                    iso2=iso2,
                                )
                                all_text += f" {title} {snippet}"
                                continue
                            found_fb_url = link
                            facebook_type = "personal_profile" if is_personal else "page"
                            status = "hit"
                            all_text += f" {title} {snippet}"
                            break

                        # Collect snippets for text extraction even if not accepted as URL
                        all_text += f" {title} {snippet}"

                except httpx.HTTPStatusError as exc:
                    status = "blocked" if exc.response.status_code in (429, 503) else "error"
                    log.warning("facebook_serper_error", query=query, error=str(exc))
                except Exception as exc:
                    status = "error"
                    log.warning("facebook_serper_error", query=query, error=str(exc))

                latency = int((time.monotonic() - t0) * 1000)
                audit_entries.append(
                    AuditEntry(
                        audit_id=audit_id,
                        source_name=self.name,
                        query=query,
                        url=_SERPER_ENDPOINT,
                        status=status,
                        latency_ms=latency,
                        notes=f"facebook_url={found_fb_url}" if found_fb_url else "",
                    )
                )

                if found_fb_url:
                    break  # stop on first match per Fix 12 spec

        # --- Fetch the found Facebook page for additional contact extraction ---
        if found_fb_url:
            await limiter.acquire()
            fetch_audit_id = str(uuid.uuid4())
            t0 = time.monotonic()
            fetch_status = "miss"
            about_url = found_fb_url.rstrip("/") + "/about"

            try:
                async with httpx.AsyncClient(
                    timeout=15, headers=_HEADERS, follow_redirects=True
                ) as fb_client:
                    for url in [about_url, found_fb_url]:
                        resp = await fb_client.get(url)
                        if resp.status_code < 400:
                            tree = HTMLParser(resp.text)
                            page_text = tree.text(separator=" ")
                            if page_text.strip():
                                all_text += " " + page_text
                                fetch_status = "hit"
                                break
                        elif resp.status_code in (302, 403, 429):
                            fetch_status = "blocked"
                            log.info("facebook_fetch_blocked", url=url, code=resp.status_code)
                            break
            except Exception as exc:
                fetch_status = "error"
                log.warning("facebook_fetch_error", url=about_url, error=str(exc))

            latency = int((time.monotonic() - t0) * 1000)
            audit_entries.append(
                AuditEntry(
                    audit_id=fetch_audit_id,
                    source_name=self.name,
                    query=found_fb_url,
                    url=about_url,
                    status=fetch_status,
                    latency_ms=latency,
                )
            )

        phones = extract_phones(all_text, company.country, self.name, self.base_confidence)

        # Fix 14: raw email extraction filtered by Facebook domain (fb_page_text only)
        # Snippet/title emails also collected via regular extract path
        fb_domain = "facebook.com"
        emails = extract_emails_raw(
            all_text, fb_domain, self.name, self.base_confidence
        )

        social = extract_social_urls(all_text)
        if found_fb_url:
            # Ensure the found URL is captured even if extract_social_urls missed it
            if social is None:
                social = SocialUrls(facebook=found_fb_url)
            elif not social.facebook:
                social = social.model_copy(update={"facebook": found_fb_url})

        source_notes = (
            "facebook_type=personal_profile" if facebook_type == "personal_profile" else ""
        )

        return SourceResult(
            source_name=self.name,
            phones=phones,
            emails=emails,
            social_urls=social,
            audit_entries=audit_entries,
            notes=source_notes,
        )
