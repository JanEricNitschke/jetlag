"""Tests: timezone-aware ``When`` handling."""

from __future__ import annotations

import datetime as dt
import zoneinfo

from jetlag_maps.config import Color, Player
from jetlag_maps.format import (
    Coordinate,
    Journey,
    SeasonFile,
    Stop,
    When,
    ensure_timezones,
    format_when,
    timezone_for,
)

_PLACE_DUCALE = Coordinate(longitude=4.72005105743093, latitude=49.7734783014379)


def _timed_stop(when: When, coordinate: Coordinate = _PLACE_DUCALE) -> Stop:
    return Stop(name="Timed", coordinate=coordinate, when=when)


def _season(stop: Stop) -> SeasonFile:
    return SeasonFile(
        season=1,
        name="Test",
        journeys=[
            Journey(
                team=[Player.SAM_DENBY, Player.BRIAN_MCMANUS],
                color=Color.RED,
                stops=[stop],
            ),
            Journey(
                team=[Player.ADAM_CHASE],
                color=Color.YELLOW,
                stops=[Stop(name="Untimed", coordinate=_PLACE_DUCALE)],
            ),
        ],
    )


def test_naive_when_round_trips_naive() -> None:
    """Legacy naive times keep their exact naive JSON form."""
    when = When.model_validate_json('{"start": "2022-07-26T06:00:00"}')
    assert when.start.tzinfo is None
    assert when.model_dump_json() == '{"start":"2022-07-26T06:00:00","end":null}'


def test_aware_when_round_trips_offset() -> None:
    """Aware times serialize with their UTC offset."""
    when = When.model_validate_json('{"start": "2022-07-26T06:00:00+02:00"}')
    assert when.start.utcoffset() == dt.timedelta(hours=2)
    assert when.model_dump_json() == '{"start":"2022-07-26T06:00:00+02:00","end":null}'


def test_timezone_for_paris() -> None:
    """The coordinate lookup resolves the containing IANA zone."""
    assert timezone_for(_PLACE_DUCALE) == "Europe/Paris"


def test_ensure_timezones_attaches_dst_aware_zone() -> None:
    """Naive times get the stop location's zone with the date's offset."""
    season = _season(_timed_stop(When(start=dt.datetime(2022, 7, 26, 6))))
    assert ensure_timezones(season)
    stop = season.journeys[0].stops[0]
    assert stop.when is not None
    assert stop.when.start.utcoffset() == dt.timedelta(hours=2)
    assert (
        stop.when.model_dump_json()
        == '{"start":"2022-07-26T06:00:00+02:00","end":null}'
    )


def test_ensure_timezones_keeps_aware_times() -> None:
    """Already aware times are never re-localized."""
    season = _season(
        _timed_stop(When(start=dt.datetime(2022, 7, 26, 6, tzinfo=dt.UTC)))
    )
    assert not ensure_timezones(season)
    stop = season.journeys[0].stops[0]
    assert stop.when is not None
    assert stop.when.start.utcoffset() == dt.timedelta(0)


def test_ensure_timezones_skips_untimed_stops() -> None:
    """Stops without a ``when`` are ignored."""
    season = SeasonFile(
        season=1,
        name="Test",
        journeys=[
            Journey(
                team=[Player.SAM_DENBY],
                color=Color.RED,
                stops=[Stop(name="Untimed", coordinate=_PLACE_DUCALE)],
            ),
            Journey(
                team=[Player.ADAM_CHASE],
                color=Color.YELLOW,
                stops=[Stop(name="Untimed", coordinate=_PLACE_DUCALE)],
            ),
        ],
    )
    assert not ensure_timezones(season)


def test_format_when_appends_offset_only_when_aware() -> None:
    """Naive times render unchanged; aware times append the offset."""
    naive = When(start=dt.datetime(2022, 7, 26, 6))
    assert format_when(naive) == "2022-07-26 06:00"
    tokyo = When(
        start=dt.datetime(2022, 10, 1, 8, tzinfo=zoneinfo.ZoneInfo("Asia/Tokyo"))
    )
    assert format_when(tokyo) == "2022-10-01 08:00 UTC+09:00"
    ranged = When(
        start=dt.datetime(2022, 7, 26, 6, tzinfo=zoneinfo.ZoneInfo("Europe/Paris")),
        end=dt.datetime(2022, 7, 26, 7, tzinfo=zoneinfo.ZoneInfo("Europe/Paris")),
    )
    assert format_when(ranged) == "2022-07-26 06:00 - 2022-07-26 07:00 UTC+02:00"
