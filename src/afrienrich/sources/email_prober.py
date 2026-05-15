"""Email pattern prober (Tier 5). Tests common patterns on a discovered domain after MX check."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import dns.resolver
import structlog
import yaml

from ..models import AuditEntry, CompanyQuery, EmailResult, SourceResult
from ..utils.rate_limiter import get_limiter
from .base import BaseSource

log = structlog.get_logger()

_PATTERNS_PATH = Path(__file__).parent.parent.parent.parent / "config" / "email_patterns.yaml"


def _load_patterns() -> list[str]:
    with open(_PATTERNS_PATH) as f:
        data: dict[str, object] = yaml.safe_load(f) or {}
    raw = data.get("patterns", ["info", "contact"])
    return list(raw) if isinstance(raw, list) else ["info", "contact"]


class EmailProberSource(BaseSource):
    name = "email_prober"
    tier = 5
    base_confidence = 50
    rate_limit_per_second = 0.2

    async def search(self, company: CompanyQuery) -> SourceResult:
        domain = company.extra.get("_discovered_domain")
        if not domain:
            return SourceResult(source_name=self.name)

        # MX check before probing
        try:
            dns.resolver.resolve(domain, "MX")
        except Exception:
            return SourceResult(
                source_name=self.name,
                audit_entries=[
                    AuditEntry(
                        audit_id=str(uuid.uuid4()),
                        source_name=self.name,
                        query=domain,
                        status="miss",
                        notes="no MX record",
                        latency_ms=0,
                    )
                ],
            )

        limiter = get_limiter(self.name)
        patterns = _load_patterns()
        emails: list[EmailResult] = []
        audit_entries: list[AuditEntry] = []

        for pattern in patterns:
            await limiter.acquire()
            address = f"{pattern}@{domain}"
            audit_id = str(uuid.uuid4())
            t0 = time.monotonic()

            # We do NOT do SMTP probing by default (gated by --smtp-probe in pipeline)
            # Just generate the candidate with syntax+MX confidence
            emails.append(
                EmailResult(
                    address=address,
                    source=self.name,
                    confidence=self.base_confidence,
                    validation_level="mx",
                    audit_id=audit_id,
                )
            )
            latency = int((time.monotonic() - t0) * 1000)
            audit_entries.append(
                AuditEntry(
                    audit_id=audit_id,
                    source_name=self.name,
                    query=address,
                    status="hit",
                    latency_ms=latency,
                )
            )
            if len(emails) >= 2:
                break

        return SourceResult(
            source_name=self.name,
            emails=emails,
            audit_entries=audit_entries,
        )
