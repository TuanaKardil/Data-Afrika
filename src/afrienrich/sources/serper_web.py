"""Serper.dev web search source (Tier 1). Queries Google SERP in the country's primary language."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import httpx
import structlog
import yaml

from ..extractors.email import extract_emails
from ..extractors.phone import extract_phones
from ..extractors.social import extract_social_urls
from ..extractors.website import extract_website
from ..models import AuditEntry, CompanyQuery, SourceResult
from ..utils.rate_limiter import get_limiter
from .base import BaseSource

log = structlog.get_logger()

_COUNTRIES_PATH = Path(__file__).parent.parent.parent.parent / "config" / "countries.yaml"
_SERPER_ENDPOINT = "https://google.serper.dev/search"

_QUERY_TEMPLATES = {
    "fr": '{name} contact téléphone email {country}',
    "en": '{name} contact phone email {country}',
    "pt": '{name} contacto telefone email {country}',
    "ar": '{name} اتصال هاتف بريد إلكتروني {country}',
}


def _country_config(iso2: str) -> dict[str, object]:
    with open(_COUNTRIES_PATH) as f:
        data: dict[str, object] = yaml.safe_load(f) or {}
    result = data.get(iso2.upper(), {})
    return result if isinstance(result, dict) else {}


class SerperWebSource(BaseSource):
    name = "serper_web"
    tier = 1
    base_confidence = 65
    rate_limit_per_second = 2.0

    def __init__(self) -> None:
        self._api_key = os.getenv("SERPER_API_KEY", "")

    async def search(self, company: CompanyQuery) -> SourceResult:
        limiter = get_limiter(self.name)
        await limiter.acquire()

        cfg = _country_config(company.country)
        lang_val = cfg.get("primary_language", "en")
        lang = str(lang_val) if lang_val else "en"
        template = _QUERY_TEMPLATES.get(lang, _QUERY_TEMPLATES["en"])
        query = template.format(name=company.importer_name, country=company.country)

        audit_id = str(uuid.uuid4())
        t0 = time.monotonic()
        status = "miss"
        snippet_text = ""
        urls: list[str] = []

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    _SERPER_ENDPOINT,
                    json={"q": query, "gl": company.country.lower(), "hl": lang, "num": 10},
                    headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()

            # Collect snippet text + URLs from organic results
            urls = []
            for item in data.get("organic", []):
                snippet_text += " " + item.get("snippet", "") + " " + item.get("title", "")
                if link := item.get("link"):
                    urls.append(link)

            status = "hit" if urls else "miss"

        except httpx.HTTPStatusError as exc:
            status = "blocked" if exc.response.status_code in (429, 503) else "error"
            log.warning("serper_web_error", status=status, error=str(exc))
        except Exception as exc:
            status = "error"
            log.warning("serper_web_error", status=status, error=str(exc))

        latency = int((time.monotonic() - t0) * 1000)
        audit = AuditEntry(
            audit_id=audit_id,
            source_name=self.name,
            query=query,
            url=_SERPER_ENDPOINT,
            status=status,
            latency_ms=latency,
        )

        phones = extract_phones(snippet_text, company.country, self.name, self.base_confidence)
        emails = extract_emails(snippet_text, self.name, self.base_confidence)
        website = extract_website(urls)
        social = extract_social_urls(snippet_text)

        return SourceResult(
            source_name=self.name,
            phones=phones,
            emails=emails,
            website=website,
            social_urls=social,
            audit_entries=[audit],
            candidate_urls=urls,
        )
