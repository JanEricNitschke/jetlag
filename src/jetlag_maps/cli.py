"""Command-line interface for jetlag-maps."""

from __future__ import annotations

import argparse
import logging
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from jetlag_maps import importer, leg_guess, render_html, render_kml, sheets, tabular
from jetlag_maps.config import GOOGLE_CREDENTIALS_FILE
from jetlag_maps.render_html import MarkerMode

if TYPE_CHECKING:
    from collections.abc import Sequence


class Command(StrEnum):
    """Available subcommands."""

    IMPORT = "import"
    EXPORT = "export"
    IMPORT_TABLE = "import-table"
    GUESS_LEGS = "guess-legs"
    RENDER = "render"
    WEB = "web"
    SYNC = "sync"
    PULL = "pull"


class ImportArgs(BaseModel):
    """Arguments of the ``import`` subcommand."""

    model_config = ConfigDict(from_attributes=True)

    kml: Path
    out: Path


class RenderArgs(BaseModel):
    """Arguments of the ``render`` subcommand."""

    model_config = ConfigDict(from_attributes=True)

    data: Path
    out: Path


class WebArgs(RenderArgs):
    """Arguments of the ``web`` subcommand."""

    markers: MarkerMode = MarkerMode.CLUSTER


class ExportArgs(BaseModel):
    """Arguments of the ``export`` subcommand."""

    model_config = ConfigDict(from_attributes=True)

    data: Path
    out: Path


class ImportTableArgs(BaseModel):
    """Arguments of the ``import-table`` subcommand."""

    model_config = ConfigDict(from_attributes=True)

    data: Path
    out: Path


class GuessLegsArgs(BaseModel):
    """Arguments of the ``guess-legs`` subcommand."""

    model_config = ConfigDict(from_attributes=True)

    data: Path


class SyncArgs(BaseModel):
    """Arguments of the manually triggered ``sync`` subcommand."""

    model_config = ConfigDict(from_attributes=True)

    data: Path
    credentials: Path = GOOGLE_CREDENTIALS_FILE


class PullArgs(BaseModel):
    """Arguments of the manually triggered ``pull`` subcommand."""

    model_config = ConfigDict(from_attributes=True)

    out: Path
    credentials: Path = GOOGLE_CREDENTIALS_FILE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jetlag-maps", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser(
        Command.IMPORT.value, help="Import KML placemarks into season JSON files."
    )
    p_import.add_argument(
        "--kml", type=Path, default=Path("All Known Jet Lag Locations.kml")
    )
    p_import.add_argument("--out", type=Path, default=Path("data"))

    p_render = sub.add_parser(
        Command.RENDER.value, help="Render season JSON file(s) as KML."
    )
    p_render.add_argument("--data", type=Path, default=Path("data"))
    p_render.add_argument("--out", type=Path, default=Path("kml"))

    p_web = sub.add_parser(
        Command.WEB.value, help="Render season JSON file(s) as interactive folium maps."
    )
    p_web.add_argument("--data", type=Path, default=Path("data"))
    p_web.add_argument("--out", type=Path, default=Path("maps"))
    p_web.add_argument(
        "--markers",
        choices=[mode.value for mode in MarkerMode],
        default=MarkerMode.CLUSTER.value,
        help="How coincident markers of different teams are shown.",
    )

    p_export = sub.add_parser(
        Command.EXPORT.value,
        help="Export season JSON file(s) to Excel/CSV tables for hand-editing.",
    )
    p_export.add_argument("--data", type=Path, default=Path("data"))
    p_export.add_argument("--out", type=Path, default=Path("export.xlsx"))

    p_import_table = sub.add_parser(
        Command.IMPORT_TABLE.value,
        help="Import Excel/CSV tables back into season JSON files.",
    )
    p_import_table.add_argument("--data", type=Path, default=Path("export.xlsx"))
    p_import_table.add_argument("--out", type=Path, default=Path("data"))

    p_guess_legs = sub.add_parser(
        Command.GUESS_LEGS.value,
        help="Fill best-guess travel modes into season JSON files.",
    )
    p_guess_legs.add_argument("--data", type=Path, default=Path("data"))

    p_sync = sub.add_parser(
        Command.SYNC.value,
        help=(
            "Manually push each season as a tab to the shared Google Sheet"
            " (never runs automatically)."
        ),
    )
    p_sync.add_argument("--data", type=Path, default=Path("data"))
    p_sync.add_argument(
        "--credentials",
        type=Path,
        default=GOOGLE_CREDENTIALS_FILE,
        help="Google service-account JSON key file with edit access to the sheet.",
    )

    p_pull = sub.add_parser(
        Command.PULL.value,
        help=(
            "Manually pull each season tab of the shared Google Sheet back"
            " into season JSON files (never runs automatically)."
        ),
    )
    p_pull.add_argument("--out", type=Path, default=Path("data"))
    p_pull.add_argument(
        "--credentials",
        type=Path,
        default=GOOGLE_CREDENTIALS_FILE,
        help="Google service-account JSON key file with read access to the sheet.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point dispatching to the import/export/render/sync/pull subcommands."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)
    match Command(args.command):
        case Command.IMPORT:
            importer.run_import(ImportArgs.model_validate(args))
        case Command.RENDER:
            render_kml.render(RenderArgs.model_validate(args))
        case Command.WEB:
            render_html.render(WebArgs.model_validate(args))
        case Command.EXPORT:
            tabular.run_export(ExportArgs.model_validate(args))
        case Command.IMPORT_TABLE:
            tabular.run_table_import(ImportTableArgs.model_validate(args))
        case Command.GUESS_LEGS:
            leg_guess.run_guess(GuessLegsArgs.model_validate(args))
        case Command.SYNC:
            sheets.run_sync(SyncArgs.model_validate(args))
        case Command.PULL:
            sheets.run_pull(PullArgs.model_validate(args))
