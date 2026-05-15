"""Extract and normalize phone numbers from raw text."""

from __future__ import annotations

import re
import uuid

import phonenumbers
from phonenumbers import PhoneNumberType, number_type

from ..models import PhoneResult

# Broad pattern to find candidate phone strings before passing to phonenumbers
_PHONE_RE = re.compile(
    r"(?<!\d)(\+?[0-9][\d\s\-().]{6,20}[0-9])(?!\d)"
)

_TYPE_MAP = {
    PhoneNumberType.MOBILE: "MOBILE",
    PhoneNumberType.FIXED_LINE: "FIXED_LINE",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "FIXED_LINE_OR_MOBILE",
    PhoneNumberType.TOLL_FREE: "TOLL_FREE",
    PhoneNumberType.VOIP: "VOIP",
}


def extract_phones(
    text: str, country_iso2: str, source: str, base_confidence: int
) -> list[PhoneResult]:
    """Return up to 5 validated PhoneResult objects parsed from text."""
    candidates = _PHONE_RE.findall(text)
    results: list[PhoneResult] = []
    seen: set[str] = set()

    for raw in candidates:
        cleaned = re.sub(r"[\s\-().]+", "", raw)
        try:
            parsed = phonenumbers.parse(cleaned, country_iso2)
        except phonenumbers.NumberParseException:
            # Try with + prefix
            try:
                parsed = phonenumbers.parse("+" + cleaned.lstrip("+"), None)
            except phonenumbers.NumberParseException:
                continue

        if not phonenumbers.is_valid_number(parsed):
            continue

        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        if e164 in seen:
            continue
        seen.add(e164)

        region = phonenumbers.region_code_for_number(parsed)
        confidence = base_confidence
        if region and region.upper() != country_iso2.upper():
            confidence -= 20

        ptype = _TYPE_MAP.get(number_type(parsed), "UNKNOWN")

        results.append(
            PhoneResult(
                number=e164,
                source=source,
                confidence=max(0, confidence),
                phone_type=ptype,
                audit_id=str(uuid.uuid4()),
            )
        )
        if len(results) >= 5:
            break

    return results
