"""Render season JSON files as KML for Google Earth / My Maps."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jetlag_maps.cli import RenderArgs


def render(args: RenderArgs) -> None:
    """Render season JSON file(s) under ``args.data`` into KML in ``args.out``."""
    raise NotImplementedError
