"""Serper.dev Maps/Places source (Tier 3). Extracts phone, address, website from Places JSON."""

from __future__ import annotations

import os
import time
import uuid

import httpx
import structlog

from ..extractors.phone import extract_phones
from ..extractors.website import extract_website
from ..models import AuditEntry, CompanyQuery, GpsCoords, SourceResult
from ..utils.rate_limiter import get_limiter
from .base import BaseSource

log = structlog.get_logger()

_SERPER_MAPS_ENDPOINT = "https://google.serper.dev/maps"


class SerperMapsSource(BaseSource):
    name = "serper_maps"
    tier = 3
    base_confidence = 70
    rate_limit_per_second = 2.0

    def __init__(self) -> None:
        self._api_key = os.getenv("SERPER_API_KEY", "")

    async def search(self, company: CompanyQuery) -> SourceResult:
        limiter = get_limiter(self.name)
        await limiter.acquire()

        query = f"{company.importer_name} {company.country}"
        audit_id = str(uuid.uuid4())
        t0 = time.monotonic()
        status = "miss"

        phones = []
        website = None
        address = None
        gps: GpsCoords | None = None

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    _SERPER_MAPS_ENDPOINT,
                    json={"q": query, "gl": company.country.lower()},
                    headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()

            places = data.get("places", [])
            if places:
                place = places[0]  # Top result
                status = "hit"
                raw_phone = place.get("phoneNumber", "")
                if raw_phone:
                    phones = extract_phones(
                        raw_phone, company.country, self.name, self.base_confidence
                    )
                raw_website = place.get("website", "")
                website = extract_website([raw_website]) if raw_website else None
                address = place.get("address")
                lat = place.get("latitude")
                lon = place.get("longitude")
                if lat is not None and lon is not None:
                    gps = GpsCoords(lat=float(lat), lon=float(lon))

        except httpx.HTTPStatusError as exc:
            status = "blocked" if exc.response.status_code in (429, 503) else "error"
            log.warning("serper_maps_error", status=status, error=str(exc))
        except Exception as exc:
            status = "error"
            log.warning("serper_maps_error", error=str(exc))

        latency = int((time.monotonic() - t0) * 1000)
        audit = AuditEntry(
            audit_id=audit_id,
            source_name=self.name,
            query=query,
            url=_SERPER_MAPS_ENDPOINT,
            status=status,
            latency_ms=latency,
        )

        return SourceResult(
            source_name=self.name,
            phones=phones,
            website=website,
            address=address,
            gps=gps,
            audit_entries=[audit],
        )
