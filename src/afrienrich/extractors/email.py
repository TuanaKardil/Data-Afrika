"""Extract and validate email addresses from raw text."""

from __future__ import annotations

import re
import uuid

from ..models import EmailResult

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_FREE_PROVIDERS = {
    "gmail.com", "yahoo.com", "yahoo.fr", "hotmail.com", "outlook.com",
    "live.com", "aol.com", "icloud.com", "protonmail.com", "gmx.com",
    "mail.com", "yandex.com", "yandex.ru",
}

FREE_PROVIDER_CONFIDENCE_CAP = 55


def extract_emails(text: str, source: str, base_confidence: int) -> list[EmailResult]:
    """Return up to 2 EmailResult objects extracted from text (syntax check only)."""
    candidates = list(dict.fromkeys(m.lower() for m in _EMAIL_RE.findall(text)))
    results: list[EmailResult] = []

    for address in candidates:
        domain = address.split("@", 1)[1] if "@" in address else ""
        confidence = base_confidence
        if domain in _FREE_PROVIDERS:
            confidence = min(confidence, FREE_PROVIDER_CONFIDENCE_CAP)

        results.append(
            EmailResult(
                address=address,
                source=source,
                confidence=max(0, confidence),
                validation_level="syntax",
                audit_id=str(uuid.uuid4()),
            )
        )
        if len(results) >= 2:
            break

    return results
