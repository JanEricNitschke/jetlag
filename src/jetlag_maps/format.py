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
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer

from .config import Color, Player


class Mode(StrEnum):
    CAR = "car"
    BIKE = "bike"
    FOOT = "foot"
    TRAIN = "train"
    PLANE = "plane"
    FERRY = "ferry"
    BUS = "bus"


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


def _format_video_time(seconds: int) -> str:
    hours, rest = divmod(seconds, _SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, _SECONDS_PER_MINUTE)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


VideoTime = Annotated[
    int,
    BeforeValidator(_parse_video_time),
    PlainSerializer(_format_video_time, return_type=str),
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
    mode: Mode
    note: str = ""
    players_override: list[Player] | None = Field(default=None, min_length=1)
    geometry: list[Coordinate] = []


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
