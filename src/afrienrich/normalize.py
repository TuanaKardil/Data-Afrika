"""Company name normalization: legal suffix stripping, embedded phone detection, ISO2 resolution."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

_COUNTRY_CONFIG: dict[str, dict[str, Any]] = {}
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "countries.yaml"

# ISO2 aliases for common country name variants
_COUNTRY_ALIASES: dict[str, str] = {
    "ivory coast": "CI",
    "côte d'ivoire": "CI",
    "cote d'ivoire": "CI",
    "nigeria": "NG",
    "senegal": "SN",
    "sénégal": "SN",
    "ghana": "GH",
    "kenya": "KE",
}

# Phone-in-name detection: matches digits with optional +, spaces, dashes, parens
_EMBEDDED_PHONE_RE = re.compile(
    r"(?<!\d)(\+?[0-9][0-9\s\-().]{6,18}[0-9])(?!\d)"
)

# Universal suffixes always stripped regardless of country
_UNIVERSAL_SUFFIXES = {
    "SARL", "SA", "SAS", "SNC", "GIE", "EPIC", "SUARL", "EIRL",
    "LTD", "LIMITED", "PLC", "LLC", "INC", "CORP", "CO",
    "LDA", "SPA", "NV", "BV", "GMBH", "AG",
}


def _load_config() -> dict[str, dict[str, Any]]:
    global _COUNTRY_CONFIG
    if not _COUNTRY_CONFIG:
        with open(_CONFIG_PATH) as f:
            _COUNTRY_CONFIG = yaml.safe_load(f) or {}
    return _COUNTRY_CONFIG


def _country_suffixes(iso2: str) -> set[str]:
    cfg = _load_config()
    country = cfg.get(iso2.upper(), {})
    return _UNIVERSAL_SUFFIXES | {s.upper() for s in country.get("legal_suffixes", [])}


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def normalize_name(name: str, country: str) -> str:
    """Return cleaned company name with legal suffixes removed."""
    iso2 = clean_country(country)
    suffixes = _country_suffixes(iso2)

    text = name.strip()
    # Remove embedded phone numbers first
    text = _EMBEDDED_PHONE_RE.sub("", text).strip()

    # Split on common separators, strip suffix tokens from the end
    tokens = re.split(r"[\s,./|–—-]+", text)
    while tokens and tokens[-1].upper().rstrip(".") in suffixes:
        tokens.pop()

    result = " ".join(tokens).strip(" ,./")
    return result if result else name.strip()


def extract_embedded_phone(name: str) -> str | None:
    """Return the first phone-like string found inside the company name field."""
    m = _EMBEDDED_PHONE_RE.search(name)
    return m.group(1).strip() if m else None


def clean_country(val: str) -> str:
    """Resolve a country value to its ISO2 code."""
    if not val:
        return ""
    stripped = val.strip()
    upper = stripped.upper()

    # Direct ISO2 match
    cfg = _load_config()
    if upper in cfg:
        return upper

    # Alias lookup
    lower = _strip_accents(stripped.lower())
    return _COUNTRY_ALIASES.get(lower, upper)
