"""Fetch real-world route geometry for ``to_next`` legs, one source per mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jetlag_maps.format import Coordinate, Mode


def fetch_geometry(mode: Mode, start: Coordinate, end: Coordinate) -> list[Coordinate]:
    """Fetch polyline geometry for a leg with ``mode`` from ``start`` to ``end``."""
    raise NotImplementedError
