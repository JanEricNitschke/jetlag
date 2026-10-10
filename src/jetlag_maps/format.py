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
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer

from .config import Color, Player

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class TravelMode(StrEnum):
    CAR = "car"
    BIKE = "bike"
    FOOT = "foot"
    TRAIN = "train"
    PLANE = "plane"
    FERRY = "ferry"
    BUS = "bus"


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
    season: int
    name: str
    journeys: list[Journey] = Field(min_length=2)

    @property
    def slug(self) -> str:
        stem = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        return f"season_{self.season:02d}_{stem}"


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
