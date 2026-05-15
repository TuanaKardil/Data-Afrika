"""Company website crawler (Tier 3). Crawls /contact and /about pages for contact info."""

from __future__ import annotations

import time
import uuid
from urllib.parse import urljoin

import httpx
import structlog
from selectolax.parser import HTMLParser

from ..extractors.address import extract_address, extract_gps
from ..extractors.email import extract_emails
from ..extractors.phone import extract_phones
from ..extractors.social import extract_social_urls
from ..models import AuditEntry, CompanyQuery, SourceResult
from ..utils.rate_limiter import get_limiter
from .base import BaseSource

log = structlog.get_logger()

_CONTACT_PATHS = [
    "/contact", "/contacts", "/contactez-nous", "/contact-us",
    "/about", "/about-us", "/a-propos", "/quem-somos", "/contato",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


class WebsiteCrawlerSource(BaseSource):
    name = "website_crawler"
    tier = 3
    base_confidence = 70
    rate_limit_per_second = 0.33

    async def search(self, company: CompanyQuery) -> SourceResult:
        # Only runs if a website was already discovered upstream
        if not company.extra.get("_discovered_website"):
            return SourceResult(source_name=self.name)

        base_url: str = company.extra["_discovered_website"]
        limiter = get_limiter(self.name)
        audit_entries: list[AuditEntry] = []
        all_text = ""

        for path in _CONTACT_PATHS:
            await limiter.acquire()
            url = urljoin(base_url, path)
            audit_id = str(uuid.uuid4())
            t0 = time.monotonic()
            page_status = "miss"

            try:
                async with httpx.AsyncClient(
                    timeout=12, headers=_HEADERS, follow_redirects=True
                ) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        tree = HTMLParser(resp.text)
                        text = tree.text(separator=" ")
                        all_text += " " + text
                        page_status = "hit"
            except httpx.HTTPStatusError as exc:
                page_status = "blocked" if exc.response.status_code in (403, 429) else "miss"
            except Exception:
                page_status = "miss"

            latency = int((time.monotonic() - t0) * 1000)
            audit_entries.append(
                AuditEntry(
                    audit_id=audit_id,
                    source_name=self.name,
                    query=base_url,
                    url=url,
                    status=page_status,
                    latency_ms=latency,
                )
            )

        phones = extract_phones(all_text, company.country, self.name, self.base_confidence)
        emails = extract_emails(all_text, self.name, self.base_confidence)
        social = extract_social_urls(all_text)
        address = extract_address(all_text)
        gps = extract_gps(all_text)

        return SourceResult(
            source_name=self.name,
            phones=phones,
            emails=emails,
            social_urls=social,
            address=address,
            gps=gps,
            audit_entries=audit_entries,
        )
