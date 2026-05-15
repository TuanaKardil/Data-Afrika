"""Unit tests for Excel reader/writer round-trip and resume logic."""

from pathlib import Path

import pandas as pd
import pytest

from afrienrich.excel.reader import read_input
from afrienrich.excel.writer import StagingWriter, finalize, load_staging
from afrienrich.models import AuditEntry, EnrichedRow


def _make_sample_xlsx(path: Path) -> None:
    df = pd.DataFrame(
        {
            "importer_name": ["ACME SARL", "BETA LTD", "GAMMA SA"],
            "country": ["CI", "NG", "SN"],
            "hs_code": ["0901", None, "1001"],
            "category_hint": ["coffee", None, "wheat"],
        }
    )
    df.to_excel(path, index=False)


class TestExcelReader:
    def test_reads_required_columns(self, tmp_path: Path) -> None:
        xlsx = tmp_path / "input.xlsx"
        _make_sample_xlsx(xlsx)
        companies = read_input(xlsx)
        assert len(companies) == 3
        assert companies[0].importer_name == "ACME SARL"
        assert companies[0].country == "CI"

    def test_assigns_row_index(self, tmp_path: Path) -> None:
        xlsx = tmp_path / "input.xlsx"
        _make_sample_xlsx(xlsx)
        companies = read_input(xlsx)
        indices = [c.row_index for c in companies]
        assert sorted(indices) == indices  # monotonically increasing

    def test_preserves_optional_columns(self, tmp_path: Path) -> None:
        xlsx = tmp_path / "input.xlsx"
        _make_sample_xlsx(xlsx)
        companies = read_input(xlsx)
        assert companies[0].hs_code == "0901"
        assert companies[0].category_hint == "coffee"

    def test_raises_on_missing_required_column(self, tmp_path: Path) -> None:
        df = pd.DataFrame({"country": ["CI", "NG"]})
        xlsx = tmp_path / "bad.xlsx"
        df.to_excel(xlsx, index=False)
        with pytest.raises((ValueError, KeyError)):
            read_input(xlsx)


class TestStagingWriter:
    def _make_row(self, idx: int = 0) -> EnrichedRow:
        return EnrichedRow(
            importer_name=f"Company {idx}",
            country="CI",
            row_index=idx,
            search_status="enriched",
            overall_confidence=75,
        )

    def test_write_and_reload(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "staging.csv"
        writer = StagingWriter(csv_path)
        writer.write_row(self._make_row(0))
        writer.write_row(self._make_row(1))
        writer.close()

        df = pd.read_csv(csv_path)
        assert len(df) == 2

    def test_load_staging_returns_indices(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "staging.csv"
        writer = StagingWriter(csv_path)
        writer.write_row(self._make_row(3))
        writer.write_row(self._make_row(7))
        writer.close()

        indices = load_staging(csv_path)
        assert 3 in indices
        assert 7 in indices

    def test_load_staging_empty_file(self, tmp_path: Path) -> None:
        indices = load_staging(tmp_path / "nonexistent.csv")
        assert indices == set()

    def test_append_mode(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "staging.csv"
        # First write
        w1 = StagingWriter(csv_path)
        w1.write_row(self._make_row(0))
        w1.close()
        # Second write (append)
        w2 = StagingWriter(csv_path)
        w2.write_row(self._make_row(1))
        w2.close()

        df = pd.read_csv(csv_path)
        assert len(df) == 2


class TestFinalize:
    def test_produces_4_sheet_xlsx(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "staging.csv"
        output = tmp_path / "out.xlsx"

        writer = StagingWriter(csv_path)
        row = EnrichedRow(
            importer_name="TEST CO",
            country="CI",
            row_index=0,
            search_status="enriched",
            overall_confidence=70,
        )
        writer.write_row(row)
        writer.close()

        audit = [
            AuditEntry(
                audit_id="abc123",
                source_name="serper_web",
                query="TEST CO",
                status="hit",
                latency_ms=200,
            )
        ]
        finalize(csv_path, output, audit, [])

        assert output.exists()
        xl = pd.ExcelFile(output)
        assert "Sonuçlar" in xl.sheet_names
        assert "Audit" in xl.sheet_names
        assert "Kaynak İstatistik" in xl.sheet_names
        assert "Hatalar" in xl.sheet_names
