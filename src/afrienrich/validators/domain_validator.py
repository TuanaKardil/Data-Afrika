"""Reject parked, for-sale, and generic platform domains."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_PARKED_PATTERNS = re.compile(
    r"(sedo\.com|godaddy\.com|namecheap\.com|hugedomains|parkingcrew|"
    r"dan\.com|afternic|flippa|escrow|domainsbyproxy|underconstruction|"
    r"coming-soon|parking)",
    re.IGNORECASE,
)

_GENERIC_PLATFORMS = {
    "facebook.com", "fb.com", "linkedin.com", "instagram.com", "twitter.com",
    "x.com", "youtube.com", "tiktok.com", "wikipedia.org",
    "google.com", "bing.com", "amazon.com", "aliexpress.com",
    "jumia.com", "jiji.com", "tontonb2b.com",
}


def is_parked_or_generic(url: str) -> bool:
    """Return True if the URL is a parked domain, for-sale page, or generic platform."""
    if not url:
        return True
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    except ValueError:
        return True
    host = parsed.netloc.lower().removeprefix("www.")
    if host in _GENERIC_PLATFORMS:
        return True
    return bool(_PARKED_PATTERNS.search(url))
