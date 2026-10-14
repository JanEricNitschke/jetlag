"""Jet Lag season structures, relevant when importing season data.

Default: 2v2 (two teams of two); a lone member marker is likely a KML
mistake. Exceptions:
- Seasons 3, 7, 9, 11, 12, 14, 16: 1v1v1 with rotating 1v2 assignments
  (three solo teams).
- Season 6: 2v2 (Adam & Ben vs Sam & Scotty), splitting up is common.
- Season 15: 2v2v2 (three pairs) that turns into a 3v3 at some point.
"""

from __future__ import annotations

import datetime as dt
import functools
import logging
import re
import zoneinfo
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer
from timezonefinder import TimezoneFinder

from .config import Color, Player

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

logger = logging.getLogger(__name__)


class TravelMode(StrEnum):
    CAR = "car"
    BIKE = "bike"
    FOOT = "foot"
    TRAIN = "train"
    PLANE = "plane"
    FERRY = "ferry"
    BUS = "bus"
    SCOOTER = "scooter"
    HELICOPTER = "helicopter"
    KAYAK = "kayak"


class GeometryQuality(StrEnum):
    """How trustworthy a leg's geometry is.

    - ``UNKNOWN``: placeholder only — the geometry is empty or a straight
      line between the stops; nothing is known about the actual path.
    - ``MODE``: the travel mode is right and the geometry is a plausible
      route for it (OSRM road/walking routing, great circles), but neither
      the actual route nor the service is confirmed.
    - ``ROUTE``: the geometry follows the actual route of a known service —
      the specific train line, bus route or flight is identified (a named
      transit line from routing, a representative real flight, or a line
      confirmed in review).
    - ``EXACT``: the actually-ridden track itself (user-provided GPX, a
      dated FlightAware scrape of the real flight).
    """

    UNKNOWN = "unknown"
    MODE = "mode"
    ROUTE = "route"
    EXACT = "exact"


_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600


class _InvalidVideoTimeError(ValueError):
    def __init__(self, value: object) -> None:
        super().__init__(f"invalid video timestamp {value!r}")


def _parse_video_time(value: object) -> int:
    match value:
        case str(text):
            parts = text.split(":")
            match parts:
                case [minutes, seconds] if (
                    minutes.isdigit()
                    and seconds.isdigit()
                    and int(seconds) < _SECONDS_PER_MINUTE
                ):
                    return int(minutes) * _SECONDS_PER_MINUTE + int(seconds)
                case [hours, minutes, seconds] if (
                    hours.isdigit()
                    and minutes.isdigit()
                    and seconds.isdigit()
                    and int(minutes) < _SECONDS_PER_MINUTE
                    and int(seconds) < _SECONDS_PER_MINUTE
                ):
                    return (
                        int(hours) * _SECONDS_PER_HOUR
                        + int(minutes) * _SECONDS_PER_MINUTE
                        + int(seconds)
                    )
                case _:
                    raise _InvalidVideoTimeError(text)
        case int(seconds) if not isinstance(value, bool) and seconds >= 0:
            return seconds
        case _:
            raise _InvalidVideoTimeError(value)


def format_video_time(seconds: int) -> str:
    """Format a video position in seconds as an ``HH:MM:SS`` string."""
    hours, rest = divmod(seconds, _SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, _SECONDS_PER_MINUTE)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


VideoTime = Annotated[
    int,
    BeforeValidator(_parse_video_time),
    PlainSerializer(format_video_time, return_type=str),
]


class Coordinate(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class When(BaseModel):
    """A stop visit in time.

    ``start``/``end`` are local wall-clock times: either naive (zone
    unknown, e.g. legacy data) or timezone-aware, serialized as ISO 8601
    with the UTC offset (e.g. ``2022-07-26T08:00:00+02:00``). Awareness
    is attached from the stop location's IANA zone on import and by
    :func:`ensure_timezones`; the resulting offsets are DST-correct for
    the visited date.
    """

    start: dt.datetime
    end: dt.datetime | None = None


class Video(BaseModel):
    episode: int
    start: VideoTime
    end: VideoTime


class ToNext(BaseModel):
    mode: TravelMode
    note: str = ""
    players_override: list[Player] | None = Field(default=None, min_length=1)
    geometry: list[Coordinate] = []
    quality: GeometryQuality = GeometryQuality.UNKNOWN


class Stop(BaseModel):
    name: str
    coordinate: Coordinate
    when: When | None = None
    video: Video | None = None
    players_override: list[Player] | None = Field(default=None, min_length=1)
    additional_players: list[Player] = []
    notes: list[str] = []
    end_of_day: list[int] = []
    to_next: list[ToNext] = []


class Journey(BaseModel):
    team: list[Player] = Field(min_length=1)
    color: Color
    stops: list[Stop] = Field(min_length=1)


class SeasonFile(BaseModel):
    season: int | float
    name: str
    journeys: list[Journey] = Field(min_length=2)

    @property
    def slug(self) -> str:
        stem = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        return f"season_{season_num_str(self.season)}_{stem}"


def season_num_str(season: float) -> str:
    """Format a season number for file/sheet stems: 9 -> ``"09"``, 13.5 -> ``"13.5"``.

    Whole seasons keep the zero-padded two-digit form; fractional seasons
    (specials like 13.5) are rendered as-is.
    """
    if isinstance(season, int):
        return f"{season:02d}"
    return f"{int(season):02d}" if float(season).is_integer() else str(season)


def load_seasons(data: Path) -> list[SeasonFile]:
    """Load one season JSON file or all season JSON files in a directory.

    Parameters
    ----------
    data
        A season JSON file or a directory containing season JSON files.

    Returns
    -------
    list[SeasonFile]
        The validated seasons, sorted by file name.
    """
    files = sorted(data.glob("*.json")) if data.is_dir() else [data]
    return [SeasonFile.model_validate_json(f.read_text("utf-8")) for f in files]


def join_players(players: Sequence[Player]) -> str:
    """Join player display names as ``"A, B & C"``."""
    names = [str(player) for player in players]
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} & {names[-1]}"


def format_when(when: When) -> str:
    """Format a visit as local ``YYYY-MM-DD HH:MM`` text.

    Naive times render as before; aware times append the UTC offset
    (e.g. ``UTC+09:00``) so visits in different zones stay comparable on
    the maps.
    """
    stamp = when.start.strftime("%Y-%m-%d %H:%M")
    if when.end is not None:
        stamp += f" - {when.end.strftime('%Y-%m-%d %H:%M')}"
    if when.start.tzinfo is not None:
        stamp += f" UTC{_format_offset(when.start)}"
    return stamp


class _UnawareMomentError(ValueError):
    def __init__(self) -> None:
        super().__init__("moment must be timezone-aware")


class _NoTimezoneFoundError(LookupError):
    def __init__(self, coordinate: Coordinate) -> None:
        super().__init__(
            f"no timezone found near {coordinate.latitude}, {coordinate.longitude}"
        )


def _format_offset(moment: dt.datetime) -> str:
    """Format a UTC offset as signed ``±HH:MM`` text."""
    total = moment.utcoffset()
    if total is None:  # only called for aware moments
        raise _UnawareMomentError
    seconds = int(total.total_seconds())
    sign = "+" if seconds >= 0 else "-"
    hours, remainder = divmod(abs(seconds), _SECONDS_PER_HOUR)
    return f"{sign}{hours:02d}:{remainder // _SECONDS_PER_MINUTE:02d}"


@functools.lru_cache(maxsize=1)
def _timezone_finder() -> TimezoneFinder:
    """Build the single shared timezone lookup (loading its polygons)."""
    return TimezoneFinder()


def timezone_for(coordinate: Coordinate) -> str:
    """Return the IANA zone name containing (or nearest to) a coordinate.

    Open-water coordinates resolve to the nautical ``Etc/GMT±X`` fixed
    zones instead of a country zone.
    """
    zone = _timezone_finder().timezone_at(
        lat=coordinate.latitude, lng=coordinate.longitude
    )
    if zone is None:
        raise _NoTimezoneFoundError(coordinate)
    return zone


def ensure_timezones(season: SeasonFile) -> bool:
    """Attach the stop location's IANA zone to naive ``When`` times.

    Aware times are left untouched even when their offset disagrees
    with the coordinate, so hand-made corrections survive. Returns
    whether any stop changed.
    """
    changed = False
    for journey in season.journeys:
        for stop in journey.stops:
            when = stop.when
            if when is None or when.start.tzinfo is not None:
                continue
            try:
                zone = zoneinfo.ZoneInfo(timezone_for(stop.coordinate))
            except LookupError:
                logger.warning(
                    "Season %s: no timezone found for %r; left naive",
                    season.season,
                    stop.name,
                )
                continue
            stop.when = When(
                start=when.start.replace(tzinfo=zone),
                end=(when.end.replace(tzinfo=zone) if when.end is not None else None),
            )
            changed = True
    return changed
