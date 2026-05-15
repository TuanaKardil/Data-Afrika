"""Extract street addresses and GPS coordinates from structured or unstructured text."""

from __future__ import annotations

import re

from ..models import GpsCoords

# GPS: lat/lon pairs like "5.3548,  -4.0083" or "lat: 5.3548 lon: -4.0083"
_GPS_RE = re.compile(
    r"(?:lat(?:itude)?[\s:=]+)?(-?\d{1,3}\.\d{4,})"
    r"[\s,;/|]+"
    r"(?:lon(?:gitude)?[\s:=]+)?(-?\d{1,3}\.\d{4,})"
)

# Simple address heuristic: "BP 1234", "01 BP 4567", "Rue ...", "Avenue ...", "Quartier ..."
_ADDR_RE = re.compile(
    r"(?:(?:0\d\s)?BP\s+\d+|"
    r"(?:Rue|Avenue|Blvd|Boulevard|Quartier|Zone Industrielle|"
    r"Industrial Estate|Plot|No\.|N°)\s+[^\n,;]{5,60})",
    re.IGNORECASE,
)


def extract_address(text: str) -> str | None:
    m = _ADDR_RE.search(text)
    return m.group(0).strip() if m else None


def extract_gps(text: str) -> GpsCoords | None:
    m = _GPS_RE.search(text)
    if not m:
        return None
    try:
        lat, lon = float(m.group(1)), float(m.group(2))
        # Basic sanity check for Africa bounding box
        if -35 <= lat <= 38 and -18 <= lon <= 52:
            return GpsCoords(lat=lat, lon=lon)
    except ValueError:
        pass
    return None
