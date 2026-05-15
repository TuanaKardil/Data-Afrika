"""Extract and validate a company website URL from search results or HTML."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_PARKED_PATTERNS = re.compile(
    r"(sedo\.com|godaddy\.com|namecheap\.com|hugedomains|parkingcrew|"
    r"dan\.com|afternic|flippa|escrow|domainsbyproxy)",
    re.IGNORECASE,
)

# Social and generic platforms that are NOT company websites
_PLATFORM_DOMAINS = {
    "facebook.com", "fb.com", "linkedin.com", "instagram.com", "twitter.com",
    "x.com", "youtube.com", "tiktok.com", "pinterest.com",
    "wikipedia.org", "wikimedia.org",
    "google.com", "bing.com", "yahoo.com",
    "amazon.com", "aliexpress.com", "jumia.com", "jiji.com",
}


def is_valid_website(url: str) -> bool:
    """Return True if the URL looks like a legitimate company website."""
    if not url:
        return False
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    except ValueError:
        return False

    host = parsed.netloc.lower().removeprefix("www.")
    if not host or "." not in host:
        return False
    if host in _PLATFORM_DOMAINS:
        return False
    return not _PARKED_PATTERNS.search(url)


def extract_website(urls: list[str]) -> str | None:
    """Return the first valid company website from a list of URL candidates."""
    for url in urls:
        cleaned = url.strip()
        if is_valid_website(cleaned):
            return cleaned if cleaned.startswith("http") else f"https://{cleaned}"
    return None


def extract_domain(url: str) -> str | None:
    """Return the registrable domain (e.g. 'example.com') from a URL."""
    if not url:
        return None
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        host = parsed.netloc.lower().removeprefix("www.")
        return host if host else None
    except ValueError:
        return None
