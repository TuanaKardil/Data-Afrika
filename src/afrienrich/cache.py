"""SQLite-backed result cache with configurable TTL. Key = sha256(source_name + query)."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .models import EnrichedRow, SourceResult

_DEFAULT_TTL_DAYS = int(os.getenv("CACHE_TTL_DAYS", "30"))
_DEFAULT_DB = Path(__file__).parent.parent.parent / "data" / "cache.db"


class Cache:
    def __init__(self, db_path: Path = _DEFAULT_DB, ttl_days: int = _DEFAULT_TTL_DAYS) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._ttl = timedelta(days=ttl_days)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def make_key(source_name: str, query: str) -> str:
        return hashlib.sha256(f"{source_name}\x00{query}".encode()).hexdigest()

    def get(self, source_name: str, query: str) -> SourceResult | None:
        key = self.make_key(source_name, query)
        row = self._conn.execute(
            "SELECT payload, created_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        payload, created_at = row
        age = datetime.utcnow() - datetime.fromisoformat(created_at)
        if age > self._ttl:
            self._conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            self._conn.commit()
            return None
        return SourceResult.model_validate_json(payload)

    def set(self, source_name: str, query: str, result: SourceResult) -> None:
        key = self.make_key(source_name, query)
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, payload, created_at) VALUES (?, ?, ?)",
            (key, result.model_dump_json(), datetime.utcnow().isoformat()),
        )
        self._conn.commit()

    def invalidate(self, source_name: str, query: str) -> None:
        key = self.make_key(source_name, query)
        self._conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        self._conn.commit()

    # --- Row-level cache (stores EnrichedRow keyed by "row:{iso2}:{normalized_name}") ---

    def get_row(self, iso2: str, normalized_name: str) -> EnrichedRow | None:
        key = hashlib.sha256(f"row:{iso2}:{normalized_name}".encode()).hexdigest()
        row = self._conn.execute(
            "SELECT payload, created_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        payload, created_at = row
        age = datetime.utcnow() - datetime.fromisoformat(created_at)
        if age > self._ttl:
            self._conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            self._conn.commit()
            return None
        return EnrichedRow.model_validate_json(payload)

    def set_row(self, iso2: str, normalized_name: str, result: EnrichedRow) -> None:
        key = hashlib.sha256(f"row:{iso2}:{normalized_name}".encode()).hexdigest()
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, payload, created_at) VALUES (?, ?, ?)",
            (key, result.model_dump_json(), datetime.utcnow().isoformat()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
