"""Import placemarks from a Google Earth KML export into season JSON files.

The KML is parsed into one :class:`~jetlag_maps.kml_parser.KmlSeason` per
season folder, assembled into :class:`~jetlag_maps.format.SeasonFile`
journeys (see :mod:`jetlag_maps.kml_converter` for the import rules), and
written as ``<slug>.json`` into the output directory. Existing files are
overwritten unconditionally.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from jetlag_maps.kml_converter import build_season
from jetlag_maps.kml_parser import parse_kml

if TYPE_CHECKING:
    from jetlag_maps.cli import ImportArgs

logger = logging.getLogger(__name__)


def run_import(args: ImportArgs) -> None:
    """Parse ``args.kml`` and write one season JSON per season into ``args.out``."""
    seasons = parse_kml(args.kml)
    if not seasons:
        logger.warning("No season folders found in %s", args.kml)
        return
    args.out.mkdir(parents=True, exist_ok=True)
    for season in seasons:
        built = build_season(season)
        if built is None:
            continue
        path = args.out / f"{built.slug}.json"
        path.write_text(built.model_dump_json(indent=2) + "\n", encoding="utf-8")
        logger.info("Wrote %s", path)
