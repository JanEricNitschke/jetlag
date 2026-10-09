"""Established routing facts for the KML import.

Facts that cannot be derived from the KML and accumulate as the data is
reviewed live here. Both structures are keyed by season number first.

Attributes
----------
EXCLUDED_MEMBERS
    Per season and placemark name, the players to drop from that
    placemark's member list. The KML aggregates "Members who visited"
    across all seasons, so players who only visited a placemark in a
    different season must be excluded here (e.g. season 1's Denver
    International Airport lists Sam Denby and Brian McManus because of
    their later-season visits).
STOP_ORDER_AFTER
    Per season, placemark names to move directly after an anchor
    placemark within each journey. Only needed for stops whose
    position cannot be derived from the video chronology; the
    chronology ordering is applied afterwards (e.g. a hypothetical
    ``{"Some Airport": "Some City Stop"}``).
"""

from __future__ import annotations

from jetlag_maps.config import Player

EXCLUDED_MEMBERS: dict[int, dict[str, frozenset[Player]]] = {
    1: {
        "Denver International Airport": frozenset(
            {Player.SAM_DENBY, Player.BRIAN_MCMANUS}
        )
    },
}

STOP_ORDER_AFTER: dict[int, dict[str, str]] = {}
