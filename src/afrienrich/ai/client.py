"""OpenRouter async HTTP client (OpenAI-compatible endpoint)."""

from __future__ import annotations

import os

import httpx

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_SITE_URL = "https://github.com/afrienrich"  # identifies the app in OpenRouter dashboard


def get_headers() -> dict[str, str]:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": _SITE_URL,
        "X-Title": "AfriEnrich",
    }


def get_async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=OPENROUTER_BASE,
        headers=get_headers(),
        timeout=30,
    )
