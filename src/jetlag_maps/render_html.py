"""Render season JSON files as interactive folium maps."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jetlag_maps.cli import WebArgs


def render(args: WebArgs) -> None:
    """Render season JSON file(s) under ``args.data`` as HTML in ``args.out``."""
    raise NotImplementedError
