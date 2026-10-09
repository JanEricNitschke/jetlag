"""Tests: Excel/CSV export/import roundtrip losslessness."""

from __future__ import annotations

from pathlib import Path

from jetlag_maps.cli import ExportArgs, ImportTableArgs
from jetlag_maps.format import load_seasons
from jetlag_maps.tabular import run_export, run_table_import


def _assert_roundtrip(committed_data: Path, tmp_path: Path, tables: Path) -> None:
    """Export the committed data to ``tables`` and re-import it identically."""
    run_export(ExportArgs(data=committed_data, out=tables))
    reimported = tmp_path / "reimported"
    run_table_import(ImportTableArgs(data=tables, out=reimported))
    assert load_seasons(reimported) == load_seasons(committed_data)


def test_csv_roundtrip_lossless(committed_data: Path, tmp_path: Path) -> None:
    """Season data exported to CSV re-imports identically."""
    _assert_roundtrip(committed_data, tmp_path, tmp_path / "tables")


def test_xlsx_roundtrip_lossless(committed_data: Path, tmp_path: Path) -> None:
    """Season data exported to Excel re-imports identically."""
    _assert_roundtrip(committed_data, tmp_path, tmp_path / "tables.xlsx")
