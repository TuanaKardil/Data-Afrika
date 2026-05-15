"""Excel input reader: validates required columns and returns CompanyQuery objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ..models import CompanyQuery

REQUIRED_COLUMNS = {"importer_name", "country"}
KNOWN_COLUMNS = {"importer_name", "country", "hs_code", "category_hint", "cleaned_name"}


def read_input(path: Path) -> list[CompanyQuery]:
    """Read INPUT.xlsx and return one CompanyQuery per row. Preserves extra columns."""
    df = pd.read_excel(path, dtype=str)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Input file missing required columns: {missing}")

    rows: list[CompanyQuery] = []
    for idx, row in df.iterrows():
        known: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            cleaned = str(val).strip() if pd.notna(val) else None
            if col in KNOWN_COLUMNS:
                known[col] = cleaned
            else:
                extra[col] = cleaned

        rows.append(
            CompanyQuery(
                importer_name=known.get("importer_name") or "",
                country=known.get("country") or "",
                hs_code=known.get("hs_code"),
                category_hint=known.get("category_hint"),
                cleaned_name=known.get("cleaned_name"),
                extra=extra,
                row_index=int(str(idx)),
            )
        )
    return rows
