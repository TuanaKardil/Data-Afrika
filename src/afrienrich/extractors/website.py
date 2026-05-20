"""Extract and validate a company website URL from search results or HTML."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_PARKED_PATTERNS = re.compile(
    r"(sedo\.com|godaddy\.com|namecheap\.com|hugedomains|parkingcrew|"
    r"dan\.com|afternic|flippa|escrow|domainsbyproxy)",
    re.IGNORECASE,
)

# Social, generic platforms, and reference/dictionary sites — not company websites
_PLATFORM_DOMAINS = {
    "facebook.com", "fb.com", "linkedin.com", "instagram.com", "twitter.com",
    "x.com", "youtube.com", "tiktok.com", "pinterest.com",
    "wikipedia.org", "wikimedia.org",
    "google.com", "bing.com", "yahoo.com",
    "amazon.com", "aliexpress.com", "jumia.com", "jiji.com",
    "larousse.fr", "dictionary.com", "merriam-webster.com", "wordreference.com",
}

# Fix 8: URL path segments that indicate a news article rather than a company homepage
_NEWS_PATH_SEGMENTS = frozenset({
    "actualite", "actualites", "actualité", "actualités",
    "news", "article", "articles",
    "presse", "communique", "communiques", "communiqué", "communiqués",
    "blog", "blogs", "reportage", "portrait", "success-story",
})

_NEWS_PATH_PREFIX_RE = re.compile(
    r"/(?:retour-de-|interview-)",
    re.IGNORECASE,
)


def _is_news_url(url: str) -> bool:
    """Return True if the URL path looks like a news article or press release."""
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    segments = {s for s in path.split("/") if s}
    if segments & _NEWS_PATH_SEGMENTS:
        return True
    return bool(_NEWS_PATH_PREFIX_RE.search(path))


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
    # Reject exact matches AND subdomains of platform domains (e.g. fr.wikipedia.org)
    if any(host == p or host.endswith("." + p) for p in _PLATFORM_DOMAINS):
        return False
    if _PARKED_PATTERNS.search(url):
        return False
    # Fix 8: reject news-article URLs masquerading as company websites
    return not _is_news_url(url)


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
