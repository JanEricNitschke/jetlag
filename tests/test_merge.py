"""Tests: the re-import merge keeps hand-made content."""

from __future__ import annotations

from jetlag_maps.config import Color, Player
from jetlag_maps.format import (
    Coordinate,
    Journey,
    SeasonFile,
    Stop,
    ToNext,
    TravelMode,
    Video,
)
from jetlag_maps.kml_converter import merge_season


def _stop(name: str, longitude: float, episode: int | None = None) -> Stop:
    video = (
        Video(episode=episode, start="00:10", end="00:20")
        if episode is not None
        else None
    )
    return Stop(
        name=name,
        coordinate=Coordinate(longitude=longitude, latitude=10.0),
        video=video,
    )


def _season(stops: list[Stop]) -> SeasonFile:
    return SeasonFile(
        season=1,
        name="Test",
        journeys=[
            Journey(
                team=[Player.SAM_DENBY, Player.BRIAN_MCMANUS],
                color=Color.RED,
                stops=stops,
            ),
            Journey(
                team=[Player.ADAM_CHASE],
                color=Color.YELLOW,
                stops=[_stop("Solo", 5.0)],
            ),
        ],
    )


def test_merge_keeps_legs_and_appends_old_stops() -> None:
    """Matched stops keep their legs; removed stops are appended."""
    fresh = _season([_stop("First", 1.0, episode=1), _stop("Second", 2.0, episode=2)])
    old = _season([_stop("First", 1.0, episode=1), _stop("Second", 2.0, episode=2)])
    old.journeys[0].stops[0].to_next = [
        ToNext(mode=TravelMode.CAR, note="hand-made", geometry=[])
    ]
    old.journeys[0].stops.append(_stop("Stopped Only In Old", 9.0, episode=3))

    merged = merge_season(fresh, old)
    stops = merged.journeys[0].stops
    assert [stop.name for stop in stops] == [
        "First",
        "Second",
        "Stopped Only In Old",
    ]
    assert stops[0].to_next[0].note == "hand-made"
    assert stops[1].to_next == []
