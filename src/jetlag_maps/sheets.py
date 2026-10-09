r"""Manual sync of season tables with the shared Google Sheet.

Both directions are strictly manual, triggered only by the user:

- ``jetlag-maps sync`` (see :func:`run_sync`) pushes every season as a
  separate tab titled ``"Season N - <Name>"`` into the shared Google
  Sheet configured in :data:`jetlag_maps.config.GOOGLE_SHEET_ID`. Each
  tab holds that season's ``stops`` and ``legs`` tables in a flat,
  readable format with the same columns as the CSV export: a stops
  header row, the stop rows, a blank separator row, then a legs header
  row and the leg rows.
- ``jetlag-maps pull`` (see :func:`run_pull`) reads every season tab of
  the shared sheet back and writes one season JSON file per season, so
  edits made in the sheet land in the repository data.

Neither command is ever called from other commands, hooks or
automation.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import gspread
from gspread.utils import ValueRenderOption

from jetlag_maps.config import GOOGLE_SHEET_ID, SEASON_TAB_NAME_OVERRIDES
from jetlag_maps.format import SeasonFile, load_seasons
from jetlag_maps.tabular import (
    LegRow,
    StopRow,
    ensure_geometry_exportable,
    export_season_tables,
    leg_rows,
    tables_to_seasons,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from gspread.spreadsheet import Spreadsheet
    from pydantic import BaseModel

    from jetlag_maps.cli import PullArgs, SyncArgs

logger = logging.getLogger(__name__)

#: Title pattern of the per-season tabs written by ``jetlag-maps sync``.
_TAB_TITLE = re.compile(r"^Season (\d+) - ")


def season_tab_name(season: SeasonFile) -> str:
    """Return the Google Sheet tab title of ``season``.

    The display name is the season's own ``name`` unless the shared-sheet
    layout of PLAN-seasons-2-9.md uses a different one, as configured in
    :data:`jetlag_maps.config.SEASON_TAB_NAME_OVERRIDES`.
    """
    name = SEASON_TAB_NAME_OVERRIDES.get(season.season, season.name)
    return f"Season {season.season} - {name}"


def season_tab_values(season: SeasonFile) -> list[list[object]]:
    """Build the cell values of ``season``'s tab, header rows included."""
    stops, legs = export_season_tables(season)
    values = _table_values(StopRow, stops)
    values.append([""] * len(StopRow.model_fields))
    values.extend(_table_values(LegRow, legs))
    return values


def _table_values(
    model: type[BaseModel], rows: Sequence[BaseModel]
) -> list[list[object]]:
    """Build a header row plus one value row per table row."""
    return [
        list(model.model_fields),
        *[list(row.model_dump().values()) for row in rows],
    ]


def _missing_credentials_message(credentials: Path) -> str:
    """Return the actionable hint printed when the key file is absent."""
    return (
        f"Service-account credentials not found at {credentials}.\n"
        "To set up `jetlag-maps sync`:\n"
        "  1. Create a Google Cloud service account with Google Sheets API\n"
        "     access and download its JSON key file.\n"
        f"  2. Save the key file as {credentials} (the path is gitignored).\n"
        "  3. Share the Google Sheet with the service account's e-mail\n"
        "     address as Editor, then re-run `jetlag-maps sync`."
    )


def _sync_worksheet(
    spreadsheet: Spreadsheet, title: str, values: list[list[object]]
) -> None:
    """Replace the ``title`` tab with ``values``, creating it if needed."""
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=title, rows=len(values), cols=max(len(row) for row in values)
        )
    worksheet.clear()
    worksheet.update(values=values)


def run_sync(args: SyncArgs) -> None:
    """Push every season of ``args.data`` as a tab to the shared Google Sheet.

    Strictly manual: this only runs when the user invokes the
    ``jetlag-maps sync`` subcommand, never from other commands or
    automation.

    Parameters
    ----------
    args
        Sync arguments; ``credentials`` is a Google service-account JSON
        key file (gitignored) whose account may edit the shared sheet.
    """
    if not args.credentials.is_file():
        raise SystemExit(_missing_credentials_message(args.credentials))
    seasons = sorted(load_seasons(args.data), key=lambda season: season.season)
    ensure_geometry_exportable(leg_rows(seasons))
    client = gspread.service_account(filename=str(args.credentials))
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    for season in seasons:
        title = season_tab_name(season)
        _sync_worksheet(spreadsheet, title, season_tab_values(season))
        logger.info("Synced %s", title)


def season_tab_rows(
    values: Sequence[Sequence[object]],
) -> tuple[list[StopRow], list[LegRow]]:
    """Parse the raw cell values of a season tab into table rows.

    Mirrors :func:`season_tab_values`: rows before the blank separator
    (after the stops header row) are stops rows, rows after the legs
    header row are legs rows. Blank rows are ignored, and rows before
    the first header row (e.g. stray notes) are skipped.
    """
    stops_header = list(StopRow.model_fields)
    legs_header = list(LegRow.model_fields)
    stops: list[StopRow] = []
    legs: list[LegRow] = []
    header: list[str] | None = None
    for row in values:
        cells = ["" if cell is None else str(cell) for cell in row]
        if not any(cells):
            continue
        if cells[: len(stops_header)] == stops_header:
            header = stops_header
            continue
        if cells[: len(legs_header)] == legs_header:
            header = legs_header
            continue
        if header is None:
            logger.debug("Skipping row before the first header row: %r", row)
            continue
        record = dict(zip(header, row, strict=False))
        if header == stops_header:
            stops.append(StopRow.model_validate(record))
        else:
            legs.append(LegRow.model_validate(record))
    return stops, legs


def run_pull(args: PullArgs) -> None:
    """Read every season tab of the shared Google Sheet into season JSON files.

    Strictly manual: this only runs when the user invokes the
    ``jetlag-maps pull`` subcommand, never from other commands or
    automation. The inverse of :func:`run_sync` — tabs whose title
    matches the ``"Season N - <Name>"`` layout are parsed and written as
    ``<season slug>.json`` files into ``args.out``.

    Parameters
    ----------
    args
        Pull arguments; ``credentials`` is a Google service-account JSON
        key file (gitignored) whose account may read the shared sheet.
    """
    if not args.credentials.is_file():
        raise SystemExit(_missing_credentials_message(args.credentials))
    client = gspread.service_account(filename=str(args.credentials))
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    stops: list[StopRow] = []
    legs: list[LegRow] = []
    for worksheet in spreadsheet.worksheets():
        if _TAB_TITLE.match(worksheet.title) is None:
            continue
        values = worksheet.get_values(value_render_option=ValueRenderOption.unformatted)
        season_stops, season_legs = season_tab_rows(values)
        stops.extend(season_stops)
        legs.extend(season_legs)
        logger.info(
            "Pulled %s (%d stops, %d legs)",
            worksheet.title,
            len(season_stops),
            len(season_legs),
        )
    seasons = tables_to_seasons(stops, legs)
    args.out.mkdir(parents=True, exist_ok=True)
    for season in seasons:
        path = args.out / f"{season.slug}.json"
        path.write_text(season.model_dump_json(indent=2) + "\n", encoding="utf-8")
        logger.info("Wrote %s", path)
