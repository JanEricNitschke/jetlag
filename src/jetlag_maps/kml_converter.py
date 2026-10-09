"""Build season JSON journeys from parsed KML placemarks.

Import rules
------------
- A stop is included in the journey of every team that has at least one
  listed member present. Outside seasons where teams split up (see
  ``_SEASON_SPLITTING_TEAMS``), one listed member implies the whole team
  was present. In splitting seasons, a partially present team sets
  ``players_override`` to the present members (reported as warnings);
  there the players are effectively four individuals traveling
  separately, so ``additional_players`` is never populated by the
  importer and stays reserved for hand-editing.
- The full placemark description becomes the stop's first note.
- Member-less placemarks are reported and skipped (icon styles in this
  export do not reliably identify member combos on their own).
- ``"... Ending Spot Day N"`` markers set ``end_of_day`` while remaining
  regular stops.
- The KML has no time data, so ``when``/``video`` stay ``None`` and
  ``to_next`` lists stay empty (filled in by later tooling).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from jetlag_maps.config import Color, Player
from jetlag_maps.format import Coordinate, Journey, SeasonFile, Stop

if TYPE_CHECKING:
    from jetlag_maps.kml_parser import KmlPlacemark, KmlSeason

logger = logging.getLogger(__name__)

_SEASON_TEAMS: dict[int, list[tuple[list[Player], Color]]] = {
    1: [
        ([Player.SAM_DENBY, Player.BRIAN_MCMANUS], Color.DARK_BLUE),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    2: [
        ([Player.SAM_DENBY, Player.JOSEPH_PISENTI], Color.ORANGE),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    3: [
        ([Player.ADAM_CHASE], Color.RED),
        ([Player.BEN_DOYLE], Color.YELLOW),
        ([Player.SAM_DENBY], Color.ORANGE),
    ],
    4: [
        ([Player.SAM_DENBY, Player.BRIAN_MCMANUS], Color.DARK_BLUE),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    5: [
        ([Player.SAM_DENBY, Player.TOBY_HENDY], Color.PURPLE),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    6: [
        ([Player.SAM_DENBY, Player.SCOTTY_ALLEN], Color.LIGHT_BLUE),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    7: [
        ([Player.ADAM_CHASE], Color.RED),
        ([Player.BEN_DOYLE], Color.YELLOW),
        ([Player.SAM_DENBY], Color.ORANGE),
    ],
    8: [
        ([Player.SAM_DENBY, Player.MICHELLE_KHARE], Color.DARK_BLUE),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    9: [
        ([Player.ADAM_CHASE], Color.RED),
        ([Player.SAM_DENBY], Color.ORANGE),
        ([Player.BEN_DOYLE], Color.YELLOW),
    ],
}

_SEASON_SPLITTING_TEAMS: frozenset[int] = frozenset({6})


def _resolve_members(
    placemark: KmlPlacemark, season_number: int, roster: set[Player]
) -> list[Player] | None:
    """Map a placemark's raw first names onto season players.

    Parameters
    ----------
    placemark
        The placemark whose member names should be resolved.
    roster
        Players participating in the season; members outside the roster are
        dropped (the KML aggregates visits across seasons) and unknown
        names are reported.

    Returns
    -------
    list[Player] | None
        Present season players in listing order, or ``None`` if no
        description or no known season member is present.
    """
    if not placemark.member_names:
        logger.warning(
            "Season %d: member-less placemark %r skipped (no description)",
            season_number,
            placemark.name,
        )
        return None
    members: list[Player] = []
    for raw in placemark.member_names:
        player = Player.from_first_name(raw)
        if player is None:
            logger.warning(
                "Season %d: unknown member name %r in %r",
                season_number,
                raw,
                placemark.name,
            )
        elif player not in roster:
            logger.debug(
                "Season %d: dropping %s from %r (not in this season's roster)",
                season_number,
                player.value,
                placemark.name,
            )
        elif player not in members:
            members.append(player)
    if not members:
        logger.warning(
            "Season %d: placemark %r has no season members in its description",
            season_number,
            placemark.name,
        )
        return None
    return members


def build_season(season: KmlSeason) -> SeasonFile | None:
    """Build a :class:`SeasonFile` from a parsed KML season folder.

    Parameters
    ----------
    season
        Parsed season folder data.

    Returns
    -------
    SeasonFile | None
        The assembled season, or ``None`` if the season has no known team
        structure.
    """
    teams = _SEASON_TEAMS.get(season.number)
    if teams is None:
        logger.warning(
            "Season %d: no team structure configured, folder skipped",
            season.number,
        )
        return None
    roster = {player for team, _color in teams for player in team}
    journeys: dict[tuple[Player, ...], list[Stop]] = {
        tuple(team): [] for team, _color in teams
    }
    splitting = season.number in _SEASON_SPLITTING_TEAMS
    for placemark in season.placemarks:
        members = _resolve_members(placemark, season.number, roster)
        if members is None:
            continue
        # Members of each team that the placemark lists as present here.
        actual = {
            tuple(team): [player for player in team if player in members]
            for team, _color in teams
        }
        for team, _color in teams:
            present = actual[tuple(team)]
            if not present:
                continue
            players_override = None
            if splitting and len(present) < len(team):
                players_override = present
                logger.warning(
                    "Season %d: only %s of team [%s] at %r (players_override set)",
                    season.number,
                    ", ".join(player.value for player in players_override),
                    ", ".join(player.value for player in team),
                    placemark.name,
                )
            journeys[tuple(team)].append(
                Stop(
                    name=placemark.name,
                    coordinate=Coordinate(
                        longitude=placemark.longitude, latitude=placemark.latitude
                    ),
                    players_override=players_override,
                    notes=[placemark.description] if placemark.description else [],
                    end_of_day=[placemark.end_of_day]
                    if placemark.end_of_day is not None
                    else [],
                )
            )
    built = [
        Journey(team=list(team), color=color, stops=journeys[tuple(team)])
        for team, color in teams
    ]
    logger.info(
        "Season %d (%s): %d placemarks -> %s",
        season.number,
        season.name,
        len(season.placemarks),
        {
            " & ".join(player.value for player in team): len(stops)
            for team, stops in journeys.items()
        },
    )
    return SeasonFile(season=season.number, name=season.name, journeys=built)
