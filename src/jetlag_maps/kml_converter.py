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
- A placemark with ``N`` episode timing lines becomes ``N`` consecutive
  stops for every visiting team, each carrying its own ``video`` segment.
  Segments of the same placemark are not necessarily separate visits; no
  travel leg will be produced between consecutive stops at the same
  coordinates (the legs stage decides). Placemarks without timings stay a
  single stop with ``video=None``.
- A description split into ``First and First:`` team sections schedules each
  section's segments, flight rows and ``datetime`` value only into the
  journey whose team set equals the section's members. A section datetime
  sets the ``when`` of the first stop of that section's journey.
- A placemark-level ``datetime:`` line sets the ``when`` of the first stop
  of the placemark in every visiting journey unless an ``Arrival:`` line
  already mapped one.
- ``Arrival:`` times become the stops' ``when`` values when they match the
  stop count and a dated flight row is present; otherwise they are appended
  to the notes and reported. Unknown ``??:??`` timing lines are appended to
  the notes of the stops with the same episode and reported.
- ``to_next`` lists stay empty (filled in by later tooling).
- Journeys are ordered by video chronology: stops with a ``video`` sort
  by ``(episode, start)`` while untimed stops keep their document
  position. This reconstructs travel patterns such as
  airport -> city -> airport -> flight from the annotations (see
  ``_chronological_order``). Hardcoded corrections that cannot be
  derived from the KML live in :mod:`jetlag_maps.import_overrides`.

Re-import merge
---------------
:func:`merge_season` merges a freshly built season into existing data:
journeys are matched by team set, stops by video segment (falling back
to name and coordinate, then name alone). A matched stop keeps the
existing ``to_next`` legs and any field the fresh import leaves empty;
existing stops without a match are appended at the end of their
journey. The importer uses this automatically, so hand-made leg
geometry and other edits survive a re-import.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
from typing import TYPE_CHECKING

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
)
from jetlag_maps.import_overrides import EXCLUDED_MEMBERS, STOP_ORDER_AFTER

if TYPE_CHECKING:
    from jetlag_maps.kml_parser import (
        KmlPlacemark,
        KmlSeason,
        _TeamSection,
        _VideoSegment,
    )

logger = logging.getLogger(__name__)

_ARRIVAL_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_FLIGHT_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_UNKNOWN_EPISODE_RE = re.compile(r"^Episode\s*(\d+)", re.IGNORECASE)
_DATETIME_RE = re.compile(
    r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+"
    r"(\d{1,2}):(\d{2})(?:\s*([AaPp][Mm]))?(?:\s+.*)?$"
)
_MONTHS: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_HOURS_PER_HALF_DAY = 12

type _Schedule = tuple[list[Video | None], list[When | None], list[list[str]]]

_END_OF_VIDEO = 1_000_000


def _resolve_excluded(
    members: list[Player], season_number: int, placemark_name: str
) -> list[Player]:
    """Drop hardcoded members excluded for this placemark."""
    excluded = EXCLUDED_MEMBERS.get(season_number, {}).get(placemark_name, frozenset())
    return [player for player in members if player not in excluded]


def _apply_order_after(journey: Journey, season_number: int) -> None:
    """Move overridden stop names directly after their anchor stop."""
    for name, anchor in STOP_ORDER_AFTER.get(season_number, {}).items():
        moved = [stop for stop in journey.stops if stop.name == name]
        anchor_stop = next(
            (stop for stop in journey.stops if stop.name == anchor), None
        )
        if not moved or anchor_stop is None:
            continue
        rest = [stop for stop in journey.stops if stop.name != name]
        index = rest.index(anchor_stop)
        journey.stops = rest[: index + 1] + moved + rest[index + 1 :]


def _chronological_order(journey: Journey) -> None:
    """Order a journey's stops by their video chronology.

    Stops with a ``video`` sort by ``(episode, start)``; stops without
    one take the chronology of the next timed stop in document order
    (or the end of the video), so untimed markers keep their document
    position. This reconstructs travel patterns like
    airport -> city -> airport -> flight from the annotations.
    """
    keys: list[tuple[int, int]] = []
    following = (_END_OF_VIDEO, 0)
    for stop in reversed(journey.stops):
        if stop.video is not None:
            following = (stop.video.episode, stop.video.start)
        keys.append(following)
    keys.reverse()
    journey.stops = [
        stop
        for _key, _index, stop in sorted(
            zip(keys, range(len(journey.stops)), journey.stops, strict=True),
            key=lambda item: item[:2],
        )
    ]


_SEASON_TEAMS: dict[int, list[tuple[list[Player], Color]]] = {
    0: [
        ([Player.SAM_DENBY], Color.DARK_BLUE),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
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
    10: [
        ([Player.SAM_DENBY, Player.TOBY_HENDY], Color.PURPLE),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    11: [
        ([Player.ADAM_CHASE], Color.RED),
        ([Player.BEN_DOYLE], Color.YELLOW),
        ([Player.SAM_DENBY], Color.ORANGE),
    ],
    12: [
        ([Player.ADAM_CHASE], Color.RED),
        ([Player.BEN_DOYLE], Color.YELLOW),
        ([Player.SAM_DENBY], Color.ORANGE),
    ],
    13: [
        ([Player.SAM_DENBY, Player.TOM_SCOTT], Color.TEAL),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    14: [
        ([Player.ADAM_CHASE], Color.RED),
        ([Player.BEN_DOYLE], Color.YELLOW),
        ([Player.SAM_DENBY], Color.ORANGE),
    ],
    15: [
        ([Player.SAM_DENBY, Player.TOBY_HENDY], Color.PURPLE),
        ([Player.ADAM_CHASE, Player.MICHELLE_KHARE], Color.DARK_BLUE),
        ([Player.BEN_DOYLE, Player.BRIAN_MCMANUS], Color.LIGHT_BLUE),
    ],
    16: [
        ([Player.ADAM_CHASE], Color.RED),
        ([Player.BEN_DOYLE], Color.YELLOW),
        ([Player.SAM_DENBY], Color.ORANGE),
    ],
    17: [
        ([Player.SAM_DENBY, Player.MICHAEL_DOWNIE], Color.DARK_GREEN),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    18: [
        ([Player.SAM_DENBY, Player.AMY_MULLER], Color.PINK),
        ([Player.ADAM_CHASE, Player.BEN_DOYLE], Color.RED),
    ],
    19: [
        ([Player.SAM_DENBY, Player.BEN_DOYLE], Color.ORANGE),
        ([Player.ADAM_CHASE, Player.TOM_SCOTT], Color.TEAL),
    ],
    20: [
        ([Player.ADAM_CHASE], Color.RED),
        ([Player.BEN_DOYLE], Color.YELLOW),
        ([Player.SAM_DENBY], Color.ORANGE),
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


def _flight_date(raw: str) -> dt.date | None:
    """Parse a ``MM/DD/YYYY`` flight date, ``None`` if invalid."""
    match = _FLIGHT_DATE_RE.match(raw)
    if match is None:
        return None
    try:
        return dt.date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
    except ValueError:
        return None


def _arrival_time(raw: str) -> dt.time | None:
    """Parse an ``HH:MM`` wall-clock arrival time, ``None`` if invalid."""
    match = _ARRIVAL_TIME_RE.match(raw)
    if match is None:
        return None
    try:
        return dt.time(int(match.group(1)), int(match.group(2)))
    except ValueError:
        return None


def _map_arrivals(
    placemark: KmlPlacemark,
    whens: list[When | None],
    extras: list[list[str]],
    season_number: int,
) -> None:
    """Map arrival times onto the placemark's stops' ``when`` fields."""
    arrivals = placemark.arrivals
    if not arrivals:
        return
    times: list[dt.time] = []
    for raw in arrivals:
        parsed = _arrival_time(raw)
        if parsed is None:
            break
        times.append(parsed)
    flight_date = (
        _flight_date(placemark.flight_rows[0].date) if placemark.flight_rows else None
    )
    if len(times) == len(whens) and flight_date is not None:
        for index, time in enumerate(times):
            whens[index] = When(start=dt.datetime.combine(flight_date, time))
        return
    for raw in arrivals:
        for notes in extras:
            notes.append(f"Arrival: {raw}")
    logger.warning(
        "Season %d: cannot map arrivals %s of %r onto %d stop(s)",
        season_number,
        arrivals,
        placemark.name,
        len(whens),
    )


def _parse_datetime(raw: str | None) -> dt.datetime | None:
    """Parse a ``D-Mon-YYYY H:MM [AM|PM]`` wall-clock value, ignoring the zone.

    Parameters
    ----------
    raw
        Raw ``datetime:`` value, or ``None``.

    Returns
    -------
    dt.datetime | None
        Naive local datetime, or ``None`` for missing or unknown values
        such as ``4-Mar-2022 ??:?? PM MST``.
    """
    if raw is None:
        return None
    match = _DATETIME_RE.match(raw.strip())
    if match is None:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None
    hour, minute = int(match.group(4)), int(match.group(5))
    meridiem = match.group(6)
    if meridiem is not None:
        if hour > _HOURS_PER_HALF_DAY:
            return None
        if meridiem.lower() == "pm" and hour != _HOURS_PER_HALF_DAY:
            hour += _HOURS_PER_HALF_DAY
        elif meridiem.lower() == "am" and hour == _HOURS_PER_HALF_DAY:
            hour = 0
    try:
        date = dt.date(int(match.group(3)), month, int(match.group(1)))
        time = dt.time(hour, minute)
    except ValueError:
        return None
    return dt.datetime.combine(date, time)


def _add_unknown_lines(
    unknown_lines: list[str],
    segments: list[_VideoSegment],
    extras: list[list[str]],
) -> None:
    """Append unknown-timing episode lines to the matching stops' notes."""
    for line in unknown_lines:
        match = _UNKNOWN_EPISODE_RE.match(line)
        episode = int(match.group(1)) if match is not None else None
        indexes = [
            index
            for index, segment in enumerate(segments)
            if segment.episode == episode
        ]
        if not indexes:
            indexes = list(range(len(extras)))
        for index in indexes:
            extras[index].append(line)


def _segment_schedule(
    segments: list[_VideoSegment], unknown_lines: list[str]
) -> tuple[list[Video | None], list[When | None], list[list[str]]]:
    """Build per-stop ``video``/``when`` values and unknown-timing notes.

    Parameters
    ----------
    segments
        Video segments of the placemark or section, in line order.
    unknown_lines
        Raw ``Episode <n> ??:?? - ??:??`` lines to append to the notes.

    Returns
    -------
    tuple[list[Video | None], list[When | None], list[list[str]]]
        One entry per segment (empty input yields one entry), holding the
        segment's video, its not-yet-mapped ``when`` (always ``None`` here)
        and the notes added by unknown timings.
    """
    count = len(segments) if segments else 1
    videos: list[Video | None] = (
        [Video(episode=s.episode, start=s.start, end=s.end) for s in segments]
        if segments
        else [None]
    )
    whens: list[When | None] = [None] * count
    extras: list[list[str]] = [[] for _ in range(count)]
    _add_unknown_lines(unknown_lines, segments, extras)
    return videos, whens, extras


def _schedule(
    placemark: KmlPlacemark, season_number: int
) -> tuple[list[Video | None], list[When | None], list[list[str]]]:
    """Build per-stop ``video``/``when`` values and extra notes for a placemark.

    Returns
    -------
    tuple[list[Video | None], list[When | None], list[list[str]]]
        One entry per segment (a placemark without timings yields one
        entry), holding the segment's video, the mapped arrival time and the
        notes added by unmappable arrivals or unknown timings.
    """
    videos, whens, extras = _segment_schedule(
        placemark.video_segments, placemark.unknown_video_lines
    )
    _map_arrivals(placemark, whens, extras, season_number)
    return videos, whens, extras


def _apply_datetime(datetime_text: str | None, whens: list[When | None]) -> None:
    """Set the first stop's ``when`` from a datetime value, if any."""
    moment = _parse_datetime(datetime_text)
    if moment is not None and whens and whens[0] is None:
        whens[0] = When(start=moment)


def _section_team(
    section: _TeamSection,
    teams: list[tuple[list[Player], Color]],
    placemark: KmlPlacemark,
    season_number: int,
) -> tuple[Player, ...] | None:
    """Resolve a section's members and match them to a journey's team set.

    Parameters
    ----------
    section
        The parsed team section.
    teams
        Configured teams of the season.
    placemark
        The placemark owning the section, used in warnings.
    season_number
        Season number used in warnings.

    Returns
    -------
    tuple[Player, ...] | None
        The journey team whose player set equals the section's resolved
        members, or ``None`` when names are unknown or no team matches.
    """
    members: list[Player] = []
    for name in section.members:
        player = Player.from_first_name(name)
        if player is None:
            logger.warning(
                "Season %d: unknown section member %r in %r",
                season_number,
                name,
                placemark.name,
            )
        elif player not in members:
            members.append(player)
    for team, _color in teams:
        if set(members) == set(team):
            return tuple(team)
    logger.warning(
        "Season %d: section %s in %r matches no journey",
        season_number,
        section.members,
        placemark.name,
    )
    return None


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
        members = _resolve_excluded(members, season.number, placemark.name)
        if not members:
            continue
        for line in placemark.unknown_video_lines:
            logger.warning(
                "Season %d: unknown video timings %r at %r",
                season.number,
                line,
                placemark.name,
            )
        # Members of each team that the placemark lists as present here.
        actual = {
            tuple(team): [player for player in team if player in members]
            for team, _color in teams
        }
        base_notes = [placemark.description] if placemark.description else []
        end_of_day = [placemark.end_of_day] if placemark.end_of_day is not None else []
        planned: list[tuple[tuple[Player, ...], _Schedule]] = []
        if placemark.sections:
            for section in placemark.sections:
                team = _section_team(section, teams, placemark, season.number)
                if team is None:
                    continue
                videos, whens, extras = _segment_schedule(
                    section.video_segments, placemark.unknown_video_lines
                )
                _apply_datetime(section.datetime, whens)
                planned.append((team, (videos, whens, extras)))
        else:
            videos, whens, extras = _schedule(placemark, season.number)
            _apply_datetime(placemark.datetime_text, whens)
            planned.extend(
                (tuple(team), (videos, whens, extras))
                for team, _color in teams
                if actual[tuple(team)]
            )
        for team, (videos, whens, extras) in planned:
            present = actual[team]
            players_override = None
            if splitting and present and len(present) < len(team):
                players_override = present
                logger.warning(
                    "Season %d: only %s of team [%s] at %r (players_override set)",
                    season.number,
                    ", ".join(player.value for player in players_override),
                    ", ".join(player.value for player in team),
                    placemark.name,
                )
            for index, video in enumerate(videos):
                journeys[team].append(
                    Stop(
                        name=placemark.name,
                        coordinate=Coordinate(
                            longitude=placemark.longitude, latitude=placemark.latitude
                        ),
                        when=whens[index],
                        video=video,
                        players_override=players_override,
                        notes=[*base_notes, *extras[index]],
                        end_of_day=[*end_of_day],
                    )
                )
    built = [
        Journey(team=list(team), color=color, stops=journeys[tuple(team)])
        for team, color in teams
    ]
    for journey in built:
        _apply_order_after(journey, season.number)
        _chronological_order(journey)
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


def merge_season(fresh: SeasonFile, existing: SeasonFile) -> SeasonFile:
    """Merge a freshly built season into existing data.

    Journeys are matched by their team member sets. Within a matched
    journey, stops are matched by name, coordinate and video segment,
    falling back to name and coordinate, then name alone. A matched
    stop keeps the existing ``to_next`` legs and any existing field the
    fresh stop does not provide. Existing stops without a match are
    appended at the end of the journey, and journeys present only in
    the existing file are appended as well.
    """
    merged: list[Journey] = []
    matched: set[int] = set()
    for journey in fresh.journeys:
        index = next(
            (
                i
                for i, old in enumerate(existing.journeys)
                if i not in matched and set(old.team) == set(journey.team)
            ),
            None,
        )
        if index is None:
            merged.append(journey)
        else:
            matched.add(index)
            merged.append(_merge_journey(journey, existing.journeys[index]))
    merged.extend(old for i, old in enumerate(existing.journeys) if i not in matched)
    return SeasonFile(season=fresh.season, name=fresh.name, journeys=merged)


def _merge_journey(fresh: Journey, old: Journey) -> Journey:
    used: set[int] = set()
    stops: list[Stop] = []
    for stop in fresh.stops:
        index = _match_stop(stop, old.stops, used)
        if index is None:
            stops.append(stop)
        else:
            used.add(index)
            stops.append(_merge_stop(stop, old.stops[index]))
    stops.extend(old_stop for i, old_stop in enumerate(old.stops) if i not in used)
    journey = Journey(team=fresh.team, color=fresh.color, stops=stops)
    _repair_leg_alignment(journey)
    return journey


_LEG_SNAP_M = 2_000.0
_LEG_SNAP_PLANE_M = 10_000.0


def _leg_snap_m(leg: ToNext) -> float:
    """Endpoint tolerance for matching a leg to consecutive stops."""
    return _LEG_SNAP_PLANE_M if leg.mode is TravelMode.PLANE else _LEG_SNAP_M


def _repair_leg_alignment(journey: Journey) -> None:
    """Re-attach legs so each connects its consecutive stops.

    Chronology ordering can change which stop follows a leg-carrying
    stop. Legs are pooled and matched to consecutive stop pairs whose
    start and end coordinates lie within ``_LEG_SNAP_M`` of the leg's
    first and last geometry points. Unmatched legs stay on their
    original stop and are reported.
    """
    pool = [
        (index, leg) for index, stop in enumerate(journey.stops) for leg in stop.to_next
    ]
    assignments: dict[int, list[ToNext]] = {
        index: [] for index in range(len(journey.stops))
    }
    used: set[int] = set()
    for index in range(len(journey.stops) - 1):
        start = journey.stops[index].coordinate
        end = journey.stops[index + 1].coordinate
        if _distance_m(start, end) < _SAME_POINT_M:
            continue
        match = next(
            (
                i
                for i, (_holder, leg) in enumerate(pool)
                if i not in used
                and leg.geometry
                and _distance_m(leg.geometry[0], start) <= _leg_snap_m(leg)
                and _distance_m(leg.geometry[-1], end) <= _leg_snap_m(leg)
            ),
            None,
        )
        if match is not None:
            used.add(match)
            assignments[index].append(pool[match][1])
    for i, (holder, leg) in enumerate(pool):
        if i not in used:
            assignments[holder].append(leg)
            logger.warning(
                "Season leg %r does not connect consecutive stops; kept in place",
                leg.note or leg.mode.value,
            )
    for index, stop in enumerate(journey.stops):
        stop.to_next = assignments[index]


_SAME_POINT_M = 30.0


def _distance_m(a: Coordinate, b: Coordinate) -> float:
    """Great-circle distance in metres between two coordinates."""
    lon1, lat1, lon2, lat2 = map(
        math.radians, (a.longitude, a.latitude, b.longitude, b.latitude)
    )
    h = (
        math.sin((lat2 - lat1) / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2.0) ** 2
    )
    return 2.0 * 6_371_000.0 * math.asin(math.sqrt(h))


def _match_stop(fresh_stop: Stop, old_stops: list[Stop], used: set[int]) -> int | None:
    for same in (_same_segment, _same_location, _same_name):
        index = next(
            (
                i
                for i, old_stop in enumerate(old_stops)
                if i not in used and same(fresh_stop, old_stop)
            ),
            None,
        )
        if index is not None:
            return index
    return None


def _same_segment(a: Stop, b: Stop) -> bool:
    return a.name == b.name and a.coordinate == b.coordinate and a.video == b.video


def _same_location(a: Stop, b: Stop) -> bool:
    return a.name == b.name and a.coordinate == b.coordinate


def _same_name(a: Stop, b: Stop) -> bool:
    return a.name == b.name


def _merge_stop(fresh_stop: Stop, old_stop: Stop) -> Stop:
    """Keep hand-made stop fields the fresh import does not provide."""
    return fresh_stop.model_copy(
        update={
            "to_next": fresh_stop.to_next or old_stop.to_next,
            "when": fresh_stop.when if fresh_stop.when is not None else old_stop.when,
            "video": (
                fresh_stop.video if fresh_stop.video is not None else old_stop.video
            ),
            "players_override": (
                fresh_stop.players_override
                if fresh_stop.players_override is not None
                else old_stop.players_override
            ),
            "additional_players": (
                fresh_stop.additional_players or old_stop.additional_players
            ),
            "notes": fresh_stop.notes or old_stop.notes,
            "end_of_day": fresh_stop.end_of_day or old_stop.end_of_day,
        }
    )
