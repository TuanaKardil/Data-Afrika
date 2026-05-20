"""Go Africa Online source (Tier 2). High-yield francophone African business directory.

GoAfrica's search page is JS-rendered; we discover listings via Serper
(site:goafricaonline.com/{iso2}) then fetch and parse the listing page directly.

Fix 10: URL slug and page title must both score >= 70 (token_set_ratio) vs company name
before the listing is accepted. If one passes and the other fails the result is accepted
with a 'directory_slug_ambiguous_match' note so the pipeline can apply a -15 penalty.
If both fail the listing is treated as a miss.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
import uuid

import httpx
import structlog
from rapidfuzz import fuzz

from ..extractors.email import extract_emails
from ..extractors.phone import extract_phones
from ..extractors.social import extract_social_urls
from ..extractors.website import extract_website
from ..models import AuditEntry, CompanyQuery, SourceResult
from ..utils.rate_limiter import get_limiter
from .base import BaseSource

log = structlog.get_logger()

_BASE_URL = "https://www.goafricaonline.com"
_SERPER_ENDPOINT = "https://google.serper.dev/search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# New GoAfrica listing URL pattern: /bf/12345-company-slug-city-country
_LISTING_URL_RE = re.compile(r"goafricaonline\.com/([a-z]{2})/(\d+-[a-z0-9-]+)")

_SLUG_THRESHOLD = 70


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _slug_from_url(url: str) -> str:
    """Extract human-readable slug from new GoAfrica URL format.

    '/bf/12345-company-name-ouaga-burkina' → 'company name'
    Strips leading digits, trailing city/country tokens.
    """
    m = _LISTING_URL_RE.search(url)
    if not m:
        return ""
    slug = m.group(2)
    slug = re.sub(r"^\d+-", "", slug)  # strip leading '12345-'
    # Strip trailing location tokens (city names, country words)
    _LOCATION_TOKENS = {
        "ouagadougou", "ouaga", "bobo", "dioulasso", "burkina", "faso",
        "abidjan", "cote", "ivoire", "dakar", "senegal", "accra", "ghana",
        "lagos", "abuja", "nigeria", "lome", "togo", "bamako", "mali",
        "conakry", "guinee", "niamey", "niger", "yaounde", "douala", "cameroun",
        "nairobi", "kenya",
    }
    parts = slug.split("-")
    # Remove trailing parts that are just location tokens
    while parts and parts[-1].lower() in _LOCATION_TOKENS:
        parts.pop()
    return " ".join(parts).strip()


def _score_against_company(text: str, company_name: str) -> int:
    """token_set_ratio of company name vs directory text (slug or page title)."""
    return int(
        fuzz.token_set_ratio(
            _strip_accents(company_name.lower()),
            _strip_accents(text.lower()),
        )
    )


async def _serper_find_listing(
    client: httpx.AsyncClient,
    company_name: str,
    iso2: str,
    api_key: str,
) -> str | None:
    """Use Serper to find the GoAfrica listing URL for a company."""
    query = f'site:goafricaonline.com/{iso2.lower()} "{company_name}"'
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    try:
        resp = await client.post(
            _SERPER_ENDPOINT,
            json={"q": query, "num": 5},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        for result in data.get("organic", []):
            link = result.get("link", "")
            if _LISTING_URL_RE.search(link):
                m = _LISTING_URL_RE.search(link)
                if m and m.group(1) == iso2.lower():
                    return link
    except Exception as exc:
        log.debug("gao_serper_error", error=str(exc))
    return None


class GoAfricaOnlineSource(BaseSource):
    name = "go_africa_online"
    tier = 2
    base_confidence = 75
    rate_limit_per_second = 0.5

    def __init__(self) -> None:
        self._api_key = os.getenv("SERPER_API_KEY", "")

    async def search(self, company: CompanyQuery) -> SourceResult:
        if not self._api_key:
            log.warning("go_africa_online_no_serper_key")
            return SourceResult(source_name=self.name)

        limiter = get_limiter(self.name)
        await limiter.acquire()

        audit_id = str(uuid.uuid4())
        t0 = time.monotonic()
        status = "miss"
        all_text = ""
        urls: list[str] = []
        result_notes = ""
        listing_url: str | None = None

        try:
            async with httpx.AsyncClient(
                timeout=15, headers=_HEADERS, follow_redirects=True
            ) as client:
                # Step 1: Find the GoAfrica listing via Serper
                listing_url = await _serper_find_listing(
                    client, company.importer_name, company.country, self._api_key
                )

                if not listing_url:
                    status = "miss"
                else:
                    # Fix 10: slug check before fetching detail page
                    slug = _slug_from_url(listing_url)
                    slug_score = _score_against_company(slug, company.importer_name)

                    # Step 2: Fetch and parse the listing page
                    await limiter.acquire()
                    detail_resp = await client.get(listing_url)
                    if detail_resp.status_code == 200:
                        from selectolax.parser import HTMLParser
                        detail_tree = HTMLParser(detail_resp.text)
                        all_text = detail_tree.text(separator=" ")

                        title_node = (
                            detail_tree.css_first("h1") or detail_tree.css_first("title")
                        )
                        page_title = title_node.text(strip=True) if title_node else ""
                        title_score = (
                            _score_against_company(page_title, company.importer_name)
                            if page_title
                            else 0
                        )

                        if slug_score < _SLUG_THRESHOLD and title_score < _SLUG_THRESHOLD:
                            status = "miss"
                            all_text = ""
                            log.debug(
                                "gao_slug_mismatch",
                                company=company.importer_name,
                                slug=slug,
                                slug_score=slug_score,
                                title_score=title_score,
                            )
                        elif slug_score < _SLUG_THRESHOLD or title_score < _SLUG_THRESHOLD:
                            status = "hit"
                            result_notes = "directory_slug_ambiguous_match"
                            log.debug(
                                "gao_slug_ambiguous",
                                company=company.importer_name,
                                slug_score=slug_score,
                                title_score=title_score,
                            )
                        else:
                            status = "hit"

                        # Collect any additional URLs found on the page
                        for a in detail_tree.css("a[href]"):
                            href = a.attributes.get("href", "")
                            if href and href.startswith("http") and "goafricaonline" not in href:
                                urls.append(href)

        except httpx.HTTPStatusError as exc:
            status = "blocked" if exc.response.status_code in (403, 429) else "error"
            log.warning("go_africa_online_error", status=status, error=str(exc))
        except Exception as exc:
            status = "error"
            log.warning("go_africa_online_error", error=str(exc))

        latency = int((time.monotonic() - t0) * 1000)
        audit = AuditEntry(
            audit_id=audit_id,
            source_name=self.name,
            query=company.importer_name,
            url=listing_url or f"serper:site:goafricaonline.com/{company.country.lower()}",
            status=status,
            latency_ms=latency,
        )

        phones = extract_phones(all_text, company.country, self.name, self.base_confidence)
        emails = extract_emails(all_text, self.name, self.base_confidence)
        website = extract_website(urls)
        social = extract_social_urls(all_text)

        return SourceResult(
            source_name=self.name,
            phones=phones,
            emails=emails,
            website=website,
            social_urls=social,
            audit_entries=[audit],
            notes=result_notes,
        )
