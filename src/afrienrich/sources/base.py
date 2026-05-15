"""Abstract base class for all data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import CompanyQuery, SourceResult


class BaseSource(ABC):
    name: str
    tier: int
    base_confidence: int
    rate_limit_per_second: float

    @abstractmethod
    async def search(self, company: CompanyQuery) -> SourceResult:
        ...
