"""Command-line interface for jetlag-maps."""

from __future__ import annotations

import argparse
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from jetlag_maps import importer, render_html, render_kml

if TYPE_CHECKING:
    from collections.abc import Sequence


class Command(StrEnum):
    """Available subcommands."""

    IMPORT = "import"
    RENDER = "render"
    WEB = "web"


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

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point dispatching to the import/render/web subcommands."""
    args = _build_parser().parse_args(argv)
    match Command(args.command):
        case Command.IMPORT:
            importer.run_import(ImportArgs.model_validate(args))
        case Command.RENDER:
            render_kml.render(RenderArgs.model_validate(args))
        case Command.WEB:
            render_html.render(WebArgs.model_validate(args))
