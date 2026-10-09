"""Import placemarks from a Google Earth KML export into season JSON files."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jetlag_maps.cli import ImportArgs


def run_import(args: ImportArgs) -> None:
    """Parse ``args.kml`` and write one season JSON per season into ``args.out``."""
    raise NotImplementedError
