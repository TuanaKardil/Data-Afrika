"""Phone validation: E.164 format, region cross-check, WhatsApp inference."""

from __future__ import annotations

import phonenumbers
from phonenumbers import PhoneNumberType, number_type

from ..models import PhoneResult

_MOBILE_TYPES = {PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE}


def validate_phone(result: PhoneResult, expected_iso2: str) -> tuple[PhoneResult, bool]:
    """Re-validate a PhoneResult and adjust confidence for region mismatch."""
    try:
        parsed = phonenumbers.parse(result.number, None)
    except phonenumbers.NumberParseException:
        return result.model_copy(update={"confidence": 0}), False

    if not phonenumbers.is_valid_number(parsed):
        return result.model_copy(update={"confidence": 0}), False

    region = phonenumbers.region_code_for_number(parsed)
    confidence = result.confidence
    if region and region.upper() != expected_iso2.upper():
        confidence = max(0, confidence - 20)

    ptype = result.phone_type
    # Infer WhatsApp-capable from mobile line type (no wa.me HEAD check per design decision)
    is_whatsapp_capable = number_type(parsed) in _MOBILE_TYPES

    updated = result.model_copy(update={"confidence": confidence, "phone_type": ptype})
    return updated, is_whatsapp_capable


def validate_phones(
    phones: list[PhoneResult], expected_iso2: str
) -> tuple[list[PhoneResult], list[str]]:
    """Validate all phones and return (validated_list, whatsapp_candidates_e164)."""
    validated: list[PhoneResult] = []
    whatsapp: list[str] = []
    for p in phones:
        result, wa = validate_phone(p, expected_iso2)
        if result.confidence > 0:
            validated.append(result)
            if wa:
                whatsapp.append(result.number)
    return validated, whatsapp
