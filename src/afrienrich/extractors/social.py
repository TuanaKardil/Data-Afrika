"""Extract social media URLs and WhatsApp indicators from HTML/text."""

from __future__ import annotations

import re

from ..models import SocialUrls

_FB_RE = re.compile(r"https?://(?:www\.|m\.)?facebook\.com/[A-Za-z0-9._/\-?=&%]+")
_LI_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9._\-/]+")
_IG_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._/\-]+")
_TW_RE = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9._/\-]+")
_WA_RE = re.compile(r"https?://(?:wa\.me|api\.whatsapp\.com/send)[^\s\"'<>]+")


def extract_social_urls(text: str) -> SocialUrls:
    """Extract the first match of each social platform from text."""

    def first(pattern: re.Pattern[str]) -> str | None:
        m = pattern.search(text)
        return m.group(0) if m else None

    return SocialUrls(
        facebook=first(_FB_RE),
        linkedin=first(_LI_RE),
        instagram=first(_IG_RE),
        twitter=first(_TW_RE),
        whatsapp=first(_WA_RE),
    )
