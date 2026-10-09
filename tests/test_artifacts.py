"""Tests: committed generated artifacts match what the JSON data produces."""

from __future__ import annotations

from pathlib import Path

from jetlag_maps.format import load_seasons
from jetlag_maps.render_kml import kml_content
from jetlag_maps.tabular import csv_tables, export_tables, xlsx_tables


def test_committed_kml_matches_data(committed_data: Path, committed_kml: Path) -> None:
    """KML rendered from the committed season JSON is byte-identical."""
    for season in load_seasons(committed_data):
        committed = (committed_kml / f"{season.slug}.kml").read_bytes()
        assert kml_content(season) == committed


def test_committed_csv_matches_data(
    committed_data: Path, committed_export: Path
) -> None:
    """The committed CSV tables are exactly what the season data produces."""
    tables = export_tables(load_seasons(committed_data))
    assert csv_tables(committed_export) == tables


def test_committed_xlsx_matches_data(
    committed_data: Path, committed_export: Path
) -> None:
    """The committed Excel file is exactly what the season data produces."""
    tables = export_tables(load_seasons(committed_data))
    assert xlsx_tables(committed_export / "export.xlsx") == tables
