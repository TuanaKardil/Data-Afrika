"""Facebook About tab source (Tier 4). Fetches public About page via httpx, no login required."""

from __future__ import annotations

import time
import uuid
from urllib.parse import quote

import httpx
import structlog
from selectolax.parser import HTMLParser

from ..extractors.email import extract_emails
from ..extractors.phone import extract_phones
from ..models import AuditEntry, CompanyQuery, SourceResult
from ..utils.rate_limiter import get_limiter
from .base import BaseSource

log = structlog.get_logger()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


class FacebookSource(BaseSource):
    name = "facebook"
    tier = 4
    base_confidence = 55
    rate_limit_per_second = 0.25

    async def search(self, company: CompanyQuery) -> SourceResult:
        limiter = get_limiter(self.name)
        await limiter.acquire()

        # Search Facebook for the company page
        search_query = quote(f"{company.importer_name} {company.country}")
        search_url = f"https://www.facebook.com/search/pages/?q={search_query}"

        audit_id = str(uuid.uuid4())
        t0 = time.monotonic()
        status = "miss"
        all_text = ""

        try:
            async with httpx.AsyncClient(
                timeout=15, headers=_HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(search_url)
                # Facebook heavily restricts unauthenticated access; gracefully degrade
                if resp.status_code == 200:
                    tree = HTMLParser(resp.text)
                    # Extract any visible text that may contain contact info
                    all_text = tree.text(separator=" ")
                    if all_text.strip():
                        status = "hit"
                elif resp.status_code in (302, 403, 429):
                    status = "blocked"
                    log.info("facebook_blocked", status_code=resp.status_code)

        except httpx.HTTPStatusError as exc:
            status = "blocked" if exc.response.status_code in (302, 403, 429) else "error"
            log.warning("facebook_error", status=status, error=str(exc))
        except Exception as exc:
            status = "error"
            log.warning("facebook_error", error=str(exc))

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

        return SourceResult(
            source_name=self.name,
            phones=phones,
            emails=emails,
            audit_entries=[audit],
        )
