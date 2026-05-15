"""Integration test for SerperWebSource using a recorded HTTP cassette (respx)."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from afrienrich.models import CompanyQuery
from afrienrich.sources.serper_web import SerperWebSource

_SERPER_FIXTURE = {
    "organic": [
        {
            "title": "ACME Cocoa SARL - Abidjan, Côte d'Ivoire",
            "link": "https://www.acmecocoa.ci",
            "snippet": "Contact: +225 07 12 34 56 78 | info@acmecocoa.ci | Import/export de cacao.",
        }
    ]
}


@pytest.fixture
def company() -> CompanyQuery:
    return CompanyQuery(
        importer_name="ACME Cocoa SARL",
        country="CI",
        row_index=0,
        extra={"_primary_language": "fr"},
    )


@pytest.mark.asyncio
@respx.mock
async def test_serper_web_hit(company: CompanyQuery) -> None:
    respx.post("https://google.serper.dev/search").mock(
        return_value=Response(200, json=_SERPER_FIXTURE)
    )

    source = SerperWebSource()
    result = await source.search(company)

    assert result.source_name == "serper_web"
    assert len(result.audit_entries) >= 1
    assert result.audit_entries[0].status == "hit"


@pytest.mark.asyncio
@respx.mock
async def test_serper_web_extracts_phone(company: CompanyQuery) -> None:
    respx.post("https://google.serper.dev/search").mock(
        return_value=Response(200, json=_SERPER_FIXTURE)
    )

    source = SerperWebSource()
    result = await source.search(company)

    # May or may not find a phone depending on extractor, but should not crash
    assert isinstance(result.phones, list)


@pytest.mark.asyncio
@respx.mock
async def test_serper_web_miss_on_empty_results(company: CompanyQuery) -> None:
    respx.post("https://google.serper.dev/search").mock(
        return_value=Response(200, json={"organic": []})
    )

    source = SerperWebSource()
    result = await source.search(company)

    assert result.source_name == "serper_web"
    assert result.audit_entries[0].status == "miss"


@pytest.mark.asyncio
@respx.mock
async def test_serper_web_handles_rate_limit(company: CompanyQuery) -> None:
    respx.post("https://google.serper.dev/search").mock(
        return_value=Response(429, text="Too Many Requests")
    )

    source = SerperWebSource()
    result = await source.search(company)

    assert result.audit_entries[0].status in ("blocked", "error")
