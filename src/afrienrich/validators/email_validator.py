"""Email validation: syntax check, MX lookup, optional SMTP RCPT probe."""

from __future__ import annotations

import smtplib

import dns.resolver
import structlog
from email_validator import EmailNotValidError, validate_email

from ..models import EmailResult

log = structlog.get_logger()

_FREE_PROVIDERS = {
    "gmail.com", "yahoo.com", "yahoo.fr", "hotmail.com", "outlook.com",
    "live.com", "aol.com", "icloud.com", "protonmail.com",
}


def validate_email_result(result: EmailResult, smtp_probe: bool = False) -> EmailResult:
    """Validate an EmailResult. Upgrades validation_level and adjusts confidence."""
    address = result.address
    confidence = result.confidence
    validation_level = result.validation_level

    # Syntax check
    try:
        info = validate_email(address, check_deliverability=False)
        address = info.normalized
    except EmailNotValidError:
        return result.model_copy(update={"confidence": 0})

    domain = address.split("@", 1)[1]

    # MX lookup
    try:
        dns.resolver.resolve(domain, "MX")
        validation_level = "mx"
        confidence = min(100, confidence + 5)
    except Exception:
        confidence = max(0, confidence - 15)
        return result.model_copy(
            update={
                "address": address,
                "confidence": confidence,
                "validation_level": validation_level,
            }
        )

    # Free provider cap after MX pass
    if domain in _FREE_PROVIDERS:
        confidence = min(confidence, 55)

    # Optional SMTP RCPT probe
    if smtp_probe:
        try:
            mx_records = dns.resolver.resolve(domain, "MX")
            mx_host = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip(".")
            with smtplib.SMTP(mx_host, timeout=5) as smtp:
                smtp.ehlo()
                code, _ = smtp.rcpt(address)
                if code == 250:
                    validation_level = "smtp"
                    confidence = min(100, confidence + 10)
        except Exception as exc:
            log.debug("smtp_probe_failed", address=address, error=str(exc))

    return result.model_copy(
        update={
            "address": address,
            "confidence": max(0, confidence),
            "validation_level": validation_level,
        }
    )
