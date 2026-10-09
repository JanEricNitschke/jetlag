r"""Round-trippable export/import of season data to Excel and CSV tables.

Season data is flattened into two tables, each with a fixed column order.
Every row is validated through the :class:`StopRow` / :class:`LegRow`
pydantic models in both directions, so external data (CSV records,
spreadsheet cells) is parsed into typed models before any conversion.

stops
    One row per stop. Columns: ``season``, ``season_name``,
    ``journey_team``, ``journey_color``, ``stop_index``, ``name``,
    ``longitude``, ``latitude``, ``when_start``, ``when_end``,
    ``video_episode``, ``video_start``, ``video_end``,
    ``players_override``, ``additional_players``, ``notes``,
    ``end_of_day``.

legs
    One row per ``to_next`` leg. Columns: ``season``, ``journey_team``,
    ``stop_index``, ``leg_index``, ``mode``, ``note``,
    ``players_override``, ``geometry``.

Cell encodings
--------------
- Player lists (``journey_team``, ``players_override``,
  ``additional_players``): full player names joined with ``"; "`` and
  parsed back by splitting on ``"; "`` (not the display-only
  :func:`jetlag_maps.format.join_players` format).
- ``end_of_day``: integers joined with ``"; "``.
- ``notes``: each note has backslashes doubled and embedded newlines
  escaped as ``\\n``; the escaped notes are then joined with ``"\\n"``.
  Notes containing newlines or backslashes therefore survive the
  round trip.
- ``geometry``: ``"lon lat"`` pairs joined with ``"; "``, e.g.
  ``"-0.12 51.5; -0.13 51.6"``.
- Empty cells mean an empty list, or ``None`` for nullable fields
  (``when``, ``video``, ``players_override``, ``when_end``).
- ``when_start``/``when_end``: ISO 8601 timestamps.
- ``video_start``/``video_end``: ``HH:MM:SS`` strings, accepted back by
  the :class:`jetlag_maps.format.VideoTime` validators.
- ``journey_color``: a ``#RRGGBB`` hex string; ``mode``: a
  :class:`jetlag_maps.format.TravelMode` value.

File layout
-----------
Every season is exported separately, once per table:

- Excel: ``--out`` is a ``.xlsx`` file holding one ``S{NN} stops`` and
  one ``S{NN} legs`` sheet per season (e.g. ``S02 stops``), with the
  same columns as the CSV files.
- CSV: ``--out`` is a directory (or a ``.csv`` path, in which case its
  parent directory is used) holding one ``season_NN_stops.csv`` and one
  ``season_NN_legs.csv`` file per season, with the same columns.
- Import reads the per-season files/sheets back; :func:`csv_tables` and
  :func:`xlsx_tables` return the combined row lists of all seasons.
- Export sorts rows by season, journey order as in the file,
  ``stop_index``, then ``leg_index``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
import sys
from typing import TYPE_CHECKING, Annotated

from openpyxl import Workbook, load_workbook
from pydantic import BaseModel, BeforeValidator

from jetlag_maps.config import Color, Player
from jetlag_maps.format import (
    Coordinate,
    Journey,
    SeasonFile,
    Stop,
    ToNext,
    TravelMode,
    Video,
    When,
    format_video_time,
    load_seasons,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from openpyxl.worksheet.worksheet import Worksheet

    from jetlag_maps.cli import ExportArgs, ImportTableArgs

logger = logging.getLogger(__name__)

# Season geometries can be very detailed polylines, so CSV records may
# exceed the default 131072-character field size of the csv module.
csv.field_size_limit(sys.maxsize)

#: Sheet-name pattern of a per-season Excel table, e.g. ``S02 stops``.
_SHEET_NAME = re.compile(r"^S(\d{2}) (stops|legs)$")

#: Maximum length of one cell's text. Excel cells hold at most 32,767
#: characters (Google Sheet cells 50,000), so any longer geometry has to
#: be split by adding a waypoint stop in the middle of the leg (see the
#: geometry-size note in PLAN-seasons-2-9.md).
MAX_CELL_CHARS = 32767


def _cell_str(value: object) -> str:
    """Normalize a raw cell value (CSV string, xlsx cell) to text."""
    return "" if value is None else str(value)


def _cell_int(value: object) -> int:
    """Normalize a raw cell value to an integer."""
    return int(float(_cell_str(value)))


def _cell_float(value: object) -> float:
    """Normalize a raw cell value to a float."""
    return float(_cell_str(value))


def _cell_optional_int(value: object) -> int | None:
    """Normalize a raw cell value to an integer, with empty cells as ``None``."""
    return None if value is None or value == "" else int(float(_cell_str(value)))


_CellStr = Annotated[str, BeforeValidator(_cell_str)]
_CellFloat = Annotated[float, BeforeValidator(_cell_float)]
_CellInt = Annotated[int, BeforeValidator(_cell_int)]
_OptionalCellInt = Annotated[int | None, BeforeValidator(_cell_optional_int)]


class StopRow(BaseModel):
    """One ``stops`` table row describing a single stop."""

    season: _CellInt
    season_name: _CellStr
    journey_team: _CellStr
    journey_color: _CellStr
    stop_index: _CellInt
    name: _CellStr
    longitude: _CellFloat
    latitude: _CellFloat
    when_start: _CellStr = ""
    when_end: _CellStr = ""
    video_episode: _OptionalCellInt = None
    video_start: _CellStr = ""
    video_end: _CellStr = ""
    players_override: _CellStr = ""
    additional_players: _CellStr = ""
    notes: _CellStr = ""
    end_of_day: _CellStr = ""


class LegRow(BaseModel):
    """One ``legs`` table row describing a single ``to_next`` leg."""

    season: _CellInt
    journey_team: _CellStr
    stop_index: _CellInt
    leg_index: _CellInt
    mode: _CellStr
    note: _CellStr = ""
    players_override: _CellStr = ""
    geometry: _CellStr = ""


_STOP_COLUMNS = tuple(StopRow.model_fields)
_LEG_COLUMNS = tuple(LegRow.model_fields)


def _encode_players(players: Sequence[Player]) -> str:
    return "; ".join(str(player) for player in players)


def _encode_ints(values: Sequence[int]) -> str:
    return "; ".join(str(value) for value in values)


def _encode_geometry(coordinates: Sequence[Coordinate]) -> str:
    return "; ".join(
        f"{coordinate.longitude} {coordinate.latitude}" for coordinate in coordinates
    )


def _decode_players(value: str) -> list[Player]:
    if not value:
        return []
    return [Player(name) for name in value.split("; ")]


def _decode_ints(value: str) -> list[int]:
    if not value:
        return []
    return [int(part) for part in value.split("; ")]


def _encode_notes(notes: Sequence[str]) -> str:
    return "\n".join(note.replace("\\", "\\\\").replace("\n", "\\n") for note in notes)


_UNESCAPES = {"\\": "\\", "n": "\n"}
_NOTE_UNESCAPE = re.compile(r"\\([\\n])")


def _decode_note(text: str) -> str:
    return _NOTE_UNESCAPE.sub(lambda match: _UNESCAPES[match.group(1)], text)


def _decode_notes(value: str) -> list[str]:
    if not value:
        return []
    return [_decode_note(note) for note in value.split("\n")]


def _decode_geometry(value: str) -> list[Coordinate]:
    if not value:
        return []
    coordinates = []
    for pair in value.split("; "):
        lon, _, lat = pair.partition(" ")
        coordinates.append(Coordinate(longitude=float(lon), latitude=float(lat)))
    return coordinates


def stop_rows(seasons: Sequence[SeasonFile]) -> list[StopRow]:
    """Build one ``stops`` row per stop of the given seasons."""
    rows: list[StopRow] = []
    for season in sorted(seasons, key=lambda season_file: season_file.season):
        for journey in season.journeys:
            for index, stop in enumerate(journey.stops):
                rows.append(
                    StopRow(
                        season=season.season,
                        season_name=season.name,
                        journey_team=_encode_players(journey.team),
                        journey_color=str(journey.color),
                        stop_index=index,
                        name=stop.name,
                        longitude=stop.coordinate.longitude,
                        latitude=stop.coordinate.latitude,
                        when_start=(stop.when.start.isoformat() if stop.when else ""),
                        when_end=(
                            stop.when.end.isoformat()
                            if stop.when is not None and stop.when.end is not None
                            else ""
                        ),
                        video_episode=stop.video.episode if stop.video else None,
                        video_start=(
                            format_video_time(stop.video.start) if stop.video else ""
                        ),
                        video_end=(
                            format_video_time(stop.video.end) if stop.video else ""
                        ),
                        players_override=(
                            _encode_players(stop.players_override)
                            if stop.players_override
                            else ""
                        ),
                        additional_players=_encode_players(stop.additional_players),
                        notes=_encode_notes(stop.notes),
                        end_of_day=_encode_ints(stop.end_of_day),
                    )
                )
    return rows


def leg_rows(seasons: Sequence[SeasonFile]) -> list[LegRow]:
    """Build one ``legs`` row per ``to_next`` leg of the given seasons."""
    rows: list[LegRow] = []
    for season in sorted(seasons, key=lambda season_file: season_file.season):
        for journey in season.journeys:
            for stop_index, stop in enumerate(journey.stops):
                for leg_index, leg in enumerate(stop.to_next):
                    rows.append(
                        LegRow(
                            season=season.season,
                            journey_team=_encode_players(journey.team),
                            stop_index=stop_index,
                            leg_index=leg_index,
                            mode=str(leg.mode),
                            note=leg.note,
                            players_override=(
                                _encode_players(leg.players_override)
                                if leg.players_override
                                else ""
                            ),
                            geometry=_encode_geometry(leg.geometry),
                        )
                    )
    return rows


def export_tables(seasons: Sequence[SeasonFile]) -> tuple[list[StopRow], list[LegRow]]:
    """Build the ``stops`` and ``legs`` table rows for the given seasons."""
    return stop_rows(seasons), leg_rows(seasons)


def ensure_geometry_exportable(legs: Sequence[LegRow]) -> None:
    """Exit with a warning if a leg's geometry exceeds :data:`MAX_CELL_CHARS`.

    Longer geometries cannot be written to Excel or Google Sheets cells;
    they must be split by adding a waypoint stop in the middle of the
    leg (see the geometry-size note in PLAN-seasons-2-9.md).

    Raises
    ------
    SystemExit
        With one warning line per oversized leg.
    """
    oversized = [row for row in legs if len(row.geometry) > MAX_CELL_CHARS]
    if not oversized:
        return
    warnings = "\n".join(
        f"  season {row.season} ({row.journey_team}), stop {row.stop_index},"
        f" leg {row.leg_index}: {len(row.geometry)} chars"
        for row in oversized
    )
    message = (
        f"Warning: {len(oversized)} legs have geometries longer than the"
        f" {MAX_CELL_CHARS}-character Excel/Google Sheets cell limit:\n"
        f"{warnings}\n"
        "Split each of these legs by adding a waypoint stop in the middle"
        " (see PLAN-seasons-2-9.md)."
    )
    raise SystemExit(message)


def export_season_tables(season: SeasonFile) -> tuple[list[StopRow], list[LegRow]]:
    """Build the ``stops`` and ``legs`` table rows of a single season."""
    return export_tables([season])


def season_csv_paths(directory: Path, season: SeasonFile) -> tuple[Path, Path]:
    """Paths of a season's per-table CSV files under ``directory``."""
    stem = f"season_{season.season:02d}"
    return directory / f"{stem}_stops.csv", directory / f"{stem}_legs.csv"


def season_sheet_names(season: SeasonFile) -> tuple[str, str]:
    """Sheet names of a season's per-table Excel sheets."""
    stem = f"S{season.season:02d}"
    return f"{stem} stops", f"{stem} legs"


def _write_season_csvs(directory: Path, season: SeasonFile) -> None:
    """Write a season's per-table CSV files."""
    stops, legs = export_season_tables(season)
    stops_path, legs_path = season_csv_paths(directory, season)
    stops_path.write_text(_csv_table(StopRow, stops), newline="", encoding="utf-8")
    legs_path.write_text(_csv_table(LegRow, legs), newline="", encoding="utf-8")


def csv_tables(path: Path) -> tuple[list[StopRow], list[LegRow]]:
    """Read the per-season ``season_NN_stops.csv``/``-``legs.csv`` tables."""
    return (
        _read_csv_files(sorted(path.glob("season_*_stops.csv")), StopRow),
        _read_csv_files(sorted(path.glob("season_*_legs.csv")), LegRow),
    )


def xlsx_tables(path: Path) -> tuple[list[StopRow], list[LegRow]]:
    """Read the per-season ``S{NN} stops``/``-``legs`` sheets of ``path``."""
    return _workbook_tables(load_workbook(path))


def tables_to_seasons(
    stops: Sequence[StopRow], legs: Sequence[LegRow]
) -> list[SeasonFile]:
    """Assemble season files from parsed ``stops`` and ``legs`` table rows."""
    return _tables_to_seasons(stops, legs)


def _csv_table[Row: BaseModel](model: type[Row], rows: Sequence[Row]) -> str:
    """Serialize table rows into the exact text of their CSV file."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=tuple(model.model_fields))
    writer.writeheader()
    writer.writerows(row.model_dump() for row in rows)
    return stream.getvalue()


def _build_workbook(seasons: Sequence[SeasonFile]) -> Workbook:
    """Build an Excel workbook holding one stops and one legs sheet per season."""
    workbook = Workbook()
    default = workbook.active
    if default is not None:
        workbook.remove(default)
    for season in sorted(seasons, key=lambda season_file: season_file.season):
        stops, legs = export_season_tables(season)
        stops_name, legs_name = season_sheet_names(season)
        _fill_sheet(workbook.create_sheet(stops_name), _STOP_COLUMNS, stops)
        _fill_sheet(workbook.create_sheet(legs_name), _LEG_COLUMNS, legs)
    return workbook


def _workbook_tables(workbook: Workbook) -> tuple[list[StopRow], list[LegRow]]:
    """Read the per-season stops/legs rows from an Excel workbook."""
    stops: list[StopRow] = []
    legs: list[LegRow] = []
    for worksheet in workbook.worksheets:
        match = _SHEET_NAME.match(worksheet.title)
        if match is None:
            continue
        if match[2] == "stops":
            stops.extend(_read_sheet(worksheet, StopRow))
        else:
            legs.extend(_read_sheet(worksheet, LegRow))
    return stops, legs


def _fill_sheet(
    worksheet: Worksheet,
    columns: tuple[str, ...],
    rows: Sequence[BaseModel],
) -> None:
    worksheet.append(list(columns))
    for row in rows:
        worksheet.append([row.model_dump()[column] for column in columns])


def _read_csv_file[Row: BaseModel](path: Path, model: type[Row]) -> list[Row]:
    with path.open("r", newline="", encoding="utf-8") as stream:
        return [model.model_validate(record) for record in csv.DictReader(stream)]


def _read_csv_files[Row: BaseModel](
    paths: Sequence[Path], model: type[Row]
) -> list[Row]:
    """Read and concatenate the table rows of several CSV files."""
    rows: list[Row] = []
    for path in paths:
        rows.extend(_read_csv_file(path, model))
    return rows


def _read_sheet[Row: BaseModel](worksheet: Worksheet, model: type[Row]) -> list[Row]:
    records = list(worksheet.iter_rows(values_only=True))
    header = [str(cell) for cell in records[0]]
    return [
        model.model_validate(dict(zip(header, record, strict=False)))
        for record in records[1:]
    ]


def _row_to_stop(row: StopRow, legs: list[ToNext]) -> Stop:
    when = None
    if row.when_start:
        when = When(
            start=dt.datetime.fromisoformat(row.when_start),
            end=dt.datetime.fromisoformat(row.when_end) if row.when_end else None,
        )
    video = None
    if row.video_episode is not None:
        video = Video(
            episode=row.video_episode, start=row.video_start, end=row.video_end
        )
    return Stop(
        name=row.name,
        coordinate=Coordinate(longitude=row.longitude, latitude=row.latitude),
        when=when,
        video=video,
        players_override=_decode_players(row.players_override) or None,
        additional_players=_decode_players(row.additional_players),
        notes=_decode_notes(row.notes),
        end_of_day=_decode_ints(row.end_of_day),
        to_next=legs,
    )


def _row_to_leg(row: LegRow) -> ToNext:
    return ToNext(
        mode=TravelMode(row.mode),
        note=row.note,
        players_override=_decode_players(row.players_override) or None,
        geometry=_decode_geometry(row.geometry),
    )


def _tables_to_seasons(
    stop_records: Sequence[StopRow], leg_records: Sequence[LegRow]
) -> list[SeasonFile]:
    legs_by_stop: dict[tuple[int, str, int], list[ToNext]] = {}
    for record in leg_records:
        key = (record.season, record.journey_team, record.stop_index)
        legs_by_stop.setdefault(key, []).append(_row_to_leg(record))
    stops_by_journey: dict[int, dict[str, list[Stop]]] = {}
    season_names: dict[int, str] = {}
    journey_colors: dict[int, dict[str, str]] = {}
    for record in sorted(stop_records, key=lambda row: (row.season, row.stop_index)):
        season = record.season
        team = record.journey_team
        stop = _row_to_stop(
            record,
            legs_by_stop.get((season, team, record.stop_index), []),
        )
        stops_by_journey.setdefault(season, {}).setdefault(team, []).append(stop)
        season_names.setdefault(season, record.season_name)
        journey_colors.setdefault(season, {})[team] = record.journey_color
    return [
        SeasonFile(
            season=season,
            name=season_names[season],
            journeys=[
                _build_journey(team, journey_colors[season][team], stops)
                for team, stops in journeys.items()
            ],
        )
        for season, journeys in stops_by_journey.items()
    ]


def _build_journey(team: str, color: str, stops: list[Stop]) -> Journey:
    return Journey(team=_decode_players(team), color=Color(color), stops=stops)


def run_export(args: ExportArgs) -> None:
    """Export season JSON file(s) as Excel or CSV tables into ``args.out``.

    Parameters
    ----------
    args
        Export arguments; ``out`` targets a ``.xlsx`` file holding one
        ``S{NN} stops`` and one ``S{NN} legs`` sheet per season, or a
        directory (or ``.csv`` path, resolved to its parent directory)
        holding one ``season_NN_stops.csv`` and ``season_NN_legs.csv``
        file per season.
    """
    seasons = load_seasons(args.data)
    if args.out.suffix.lower() == ".xlsx":
        ensure_geometry_exportable(leg_rows(seasons))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        _build_workbook(seasons).save(args.out)
        logger.info("Wrote %s", args.out)
    else:
        directory = args.out.parent if args.out.suffix.lower() == ".csv" else args.out
        directory.mkdir(parents=True, exist_ok=True)
        for season in seasons:
            _write_season_csvs(directory, season)
        logger.info("Wrote per-season CSV tables to %s", directory)


def run_table_import(args: ImportTableArgs) -> None:
    """Import Excel or CSV tables from ``args.data`` into season JSON files.

    Parameters
    ----------
    args
        Import-table arguments; ``data`` is a ``.xlsx`` file holding one
        ``S{NN} stops`` and one ``S{NN} legs`` sheet per season, or a
        directory (or ``.csv`` path, resolved to its parent directory)
        holding one ``season_NN_stops.csv`` / ``season_NN_legs.csv`` pair
        per season; ``out`` receives one JSON file per season, named by
        the season slug.
    """
    if args.data.suffix.lower() == ".xlsx":
        stops, legs = xlsx_tables(args.data)
    else:
        directory = (
            args.data.parent if args.data.suffix.lower() == ".csv" else args.data
        )
        stops, legs = csv_tables(directory)
    seasons = _tables_to_seasons(stops, legs)
    args.out.mkdir(parents=True, exist_ok=True)
    for season in seasons:
        path = args.out / f"{season.slug}.json"
        path.write_text(season.model_dump_json(indent=2) + "\n", encoding="utf-8")
        logger.info("Wrote %s", path)
