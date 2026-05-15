"""Go Africa Online source (Tier 2). High-yield francophone African business directory."""

from __future__ import annotations

import time
import uuid
from urllib.parse import quote

import httpx
import structlog
from selectolax.parser import HTMLParser

from ..extractors.email import extract_emails
from ..extractors.phone import extract_phones
from ..extractors.social import extract_social_urls
from ..extractors.website import extract_website
from ..models import AuditEntry, CompanyQuery, SourceResult
from ..utils.rate_limiter import get_limiter
from .base import BaseSource

log = structlog.get_logger()

_BASE_URL = "https://www.goafricaonline.com"
_SEARCH_URL = _BASE_URL + "/{iso2}/recherche/?q={query}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


class GoAfricaOnlineSource(BaseSource):
    name = "go_africa_online"
    tier = 2
    base_confidence = 75
    rate_limit_per_second = 0.5

    async def search(self, company: CompanyQuery) -> SourceResult:
        limiter = get_limiter(self.name)
        await limiter.acquire()

        iso2 = company.country.lower()
        query = quote(company.importer_name)
        search_url = _SEARCH_URL.format(iso2=iso2, query=query)

        audit_id = str(uuid.uuid4())
        t0 = time.monotonic()
        status = "miss"
        all_text = ""
        urls: list[str] = []

        try:
            async with httpx.AsyncClient(
                timeout=15, headers=_HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(search_url)
                resp.raise_for_status()
                tree = HTMLParser(resp.text)

                # Find first company listing link
                for a in tree.css("a[href*='/annonces/']"):
                    href = a.attributes.get("href", "")
                    if href:
                        detail_url = href if href.startswith("http") else _BASE_URL + href
                        urls.append(detail_url)
                        break

                # Fetch detail page if found
                if urls:
                    await limiter.acquire()
                    detail_resp = await client.get(urls[0])
                    if detail_resp.status_code == 200:
                        detail_tree = HTMLParser(detail_resp.text)
                        all_text = detail_tree.text(separator=" ")
                        status = "hit"

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
            url=search_url,
            status=status,
            latency_ms=latency,
        )

        phones = extract_phones(all_text, company.country, self.name, self.base_confidence)
        emails = extract_emails(all_text, self.name, self.base_confidence)
        website = extract_website(urls[1:])  # Any additional URLs found
        social = extract_social_urls(all_text)

        return SourceResult(
            source_name=self.name,
            phones=phones,
            emails=emails,
            website=website,
            social_urls=social,
            audit_entries=[audit],
        )
