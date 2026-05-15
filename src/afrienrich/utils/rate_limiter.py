"""Async token-bucket rate limiter, one instance per domain, configured from sources.yaml."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import yaml

_SOURCES_CONFIG: dict[str, dict[str, object]] = {}
_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "sources.yaml"


def _load_limits() -> dict[str, float]:
    global _SOURCES_CONFIG
    if not _SOURCES_CONFIG:
        with open(_CONFIG_PATH) as f:
            data = yaml.safe_load(f) or {}
        _SOURCES_CONFIG = data.get("sources", {})
    result: dict[str, float] = {}
    for name, cfg in _SOURCES_CONFIG.items():
        val = cfg.get("rate_limit_per_second", 1.0)
        result[name] = float(val) if isinstance(val, (int, float)) else 1.0
    return result


class RateLimiter:
    """Simple async token-bucket rate limiter."""

    def __init__(self, rate_per_second: float) -> None:
        self._rate = rate_per_second
        self._min_interval = 1.0 / rate_per_second if rate_per_second > 0 else 0.0
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            wait = self._min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


_limiters: dict[str, RateLimiter] = {}


def get_limiter(source_name: str) -> RateLimiter:
    """Return (or create) a RateLimiter for the given source."""
    if source_name not in _limiters:
        limits = _load_limits()
        rate = limits.get(source_name, 1.0)
        _limiters[source_name] = RateLimiter(rate)
    return _limiters[source_name]
