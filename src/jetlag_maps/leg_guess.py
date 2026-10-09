"""Fill best-guess travel modes into season data for manual correction.

Every stop is classified by the transport infrastructure that lies
within about one kilometre of it, queried from OpenStreetMap through the
Overpass API (with mirror fallback). Classifications are persisted in
``~/.cache/jetlag-maps/stop_classes.json`` keyed by rounded coordinate,
so repeated runs cost no Overpass requests. Stops whose name contains
``"airport"`` or ``"aerodrome"`` take a name-based fast path; every
other name (e.g. ``"... Station"``) is confirmed by Overpass because a
name does not prove infrastructure (``"Power Station"`` is not a
railway station).

Based on the classifications, one mode-only ``to_next`` leg is guessed
for each pair of consecutive stops of each journey:

========  ====================================================
< 50 m    no leg
< 2 km    foot
ferry     both stops have a ferry terminal nearby
train     both stops have a train station nearby, at most
          500 km apart
bus       both stops have a bus station nearby and neither
          has a train station
plane     both stops have an airport nearby and more than
          150 km apart
otherwise car
========  ====================================================

Stops that already carry ``to_next`` legs are left untouched; no
geometry, ``when`` or ``video`` data is guessed. Season 1 only uses
foot, car and plane (see ``_SEASON_ALLOWED_MODES``): preferred modes
outside that set fall back to car. Every run also writes
``leg_guesses.txt`` into the current directory: one line per stop pair
in stop order (the best available chronological estimate), listing
season, team, the connected stops, the distance and the guessed mode.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from pydantic import BaseModel, TypeAdapter, ValidationError

from jetlag_maps.format import (
    Coordinate,
    SeasonFile,
    ToNext,
    TravelMode,
    join_players,
    load_seasons,
)

if TYPE_CHECKING:
    from jetlag_maps.cli import GuessLegsArgs

logger = logging.getLogger(__name__)

_OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
_USER_AGENT = "jetlag-maps/0.1 (leg mode guessing)"
_REQUEST_TIMEOUT = 60.0
_OVERPASS_TIMEOUT_S = 60.0
_QUERY_SPACING_S = 1.0
_RETRY_SPACING_S = 5.0
_MIRROR_ATTEMPTS = 3
_CLASS_RADIUS_M = 1000.0
_MIN_LEG_M = 50.0
_MAX_FOOT_M = 2000.0
_MAX_TRAIN_M = 500_000.0
_MIN_PLANE_M = 150_000.0
_EARTH_RADIUS_M = 6_371_000.0
_CACHE_FILE = Path.home() / ".cache" / "jetlag-maps" / "stop_classes.json"
_REPORT_FILE = Path("leg_guesses.txt")
_AIRPORT_NAME_RE = re.compile(r"airport|aerodrome", re.IGNORECASE)

_SEASON_ALLOWED_MODES: dict[int, frozenset[TravelMode]] = {
    1: frozenset({TravelMode.FOOT, TravelMode.CAR, TravelMode.PLANE}),
}


class StopClass(StrEnum):
    """A kind of transport infrastructure near a stop."""

    AIRPORT = "airport"
    TRAIN_STATION = "train_station"
    BUS_STATION = "bus_station"
    FERRY_TERMINAL = "ferry_terminal"


class _ClassSource(StrEnum):
    """How the classification of a stop was obtained."""

    NAME = "name-based"
    OVERPASS = "overpass"
    CACHE = "cache"


_cache_model = TypeAdapter(dict[str, list[StopClass]])


class GuessError(RuntimeError):
    """A stop classification could not be obtained."""


class _OverpassPoint(BaseModel):
    """A latitude/longitude position as returned by the Overpass API."""

    lat: float
    lon: float


class _OverpassElement(BaseModel):
    """An element of an Overpass ``out tags center`` response."""

    type: str
    lat: float | None = None
    lon: float | None = None
    center: _OverpassPoint | None = None
    tags: dict[str, str] = {}


class _OverpassResponse(BaseModel):
    """The top level of an Overpass stop classification response."""

    elements: list[_OverpassElement] = []
    remark: str = ""


def run_guess(args: GuessLegsArgs) -> None:
    """Guess a travel mode for every leg-less stop pair under ``args.data``.

    Parameters
    ----------
    args
        Guess-legs arguments; ``data`` is a season JSON file or a
        directory of season JSON files. Each season file whose stops
        gained new ``to_next`` legs is rewritten in place.
    """
    seasons = load_seasons(args.data)
    directory = args.data if args.data.is_dir() else args.data.parent
    cache = _load_cache()
    classes, sources = _classify_stops(seasons, cache)
    _save_cache(cache)
    report: list[str] = []
    for season in seasons:
        mode_counts, skipped, rows = _fill_season_legs(season, classes)
        report.extend(rows)
        _write_season(directory, season)
        _log_season(season, mode_counts, skipped, sources)
    _REPORT_FILE.write_text("\n".join(report) + "\n", encoding="utf-8")
    logger.info("Wrote %s", _REPORT_FILE)


def _classify_stops(
    seasons: list[SeasonFile], cache: dict[str, list[StopClass]]
) -> tuple[dict[str, frozenset[StopClass]], dict[str, _ClassSource]]:
    """Classify every unique stop coordinate, updating ``cache`` in place."""
    named: dict[str, Coordinate] = {}
    names: dict[str, str] = {}
    for season in seasons:
        for journey in season.journeys:
            for stop in journey.stops:
                key = _cache_key(stop.coordinate)
                if key not in named:
                    named[key] = stop.coordinate
                    names[key] = stop.name
    classes: dict[str, frozenset[StopClass]] = {}
    sources: dict[str, _ClassSource] = {}
    waited = False
    total = len(named)
    for index, (key, coordinate) in enumerate(named.items(), start=1):
        if key in cache:
            classes[key] = frozenset(cache[key])
            sources[key] = _ClassSource.CACHE
            continue
        if _AIRPORT_NAME_RE.search(names[key]) is not None:
            classes[key] = frozenset({StopClass.AIRPORT})
            sources[key] = _ClassSource.NAME
        else:
            logger.info("[%d/%d] classifying %r...", index, total, names[key])
            if waited:
                time.sleep(_QUERY_SPACING_S)
            classes[key] = _overpass_classes(coordinate)
            sources[key] = _ClassSource.OVERPASS
            waited = True
        cache[key] = sorted(classes[key])
        _save_cache(cache)
    return classes, sources


def _fill_season_legs(
    season: SeasonFile, classes: dict[str, frozenset[StopClass]]
) -> tuple[Counter[TravelMode], int, list[str]]:
    """Append one guessed leg per leg-less stop pair of ``season``.

    Returns
    -------
    tuple[Counter[TravelMode], int, list[str]]
        The number of guessed legs per mode, the number of stop pairs
        skipped because they are less than 50 m apart, and one
        chronological report line per stop pair of every journey.
    """
    mode_counts: Counter[TravelMode] = Counter()
    skipped = 0
    report: list[str] = []
    allowed = _SEASON_ALLOWED_MODES.get(season.season, frozenset(TravelMode))
    for journey in season.journeys:
        for stop, nxt in pairwise(journey.stops):
            distance = _haversine_m(
                (stop.coordinate.longitude, stop.coordinate.latitude),
                (nxt.coordinate.longitude, nxt.coordinate.latitude),
            )
            if stop.to_next:
                mode = "existing"
            elif distance < _MIN_LEG_M:
                skipped += 1
                mode = "skip (<50 m)"
            else:
                guessed = _guess_mode(
                    classes[_cache_key(stop.coordinate)],
                    classes[_cache_key(nxt.coordinate)],
                    distance,
                    allowed,
                )
                stop.to_next.append(ToNext(mode=guessed, note="", geometry=[]))
                mode_counts[guessed] += 1
                mode = guessed.value
            report.append(
                f"S{season.season:02d} | {join_players(journey.team)} | "
                f"{stop.name} -> {nxt.name} | {distance / 1000:.1f} km | {mode}"
            )
    return mode_counts, skipped, report


def _guess_mode(
    start: frozenset[StopClass],
    destination: frozenset[StopClass],
    distance_m: float,
    allowed: frozenset[TravelMode],
) -> TravelMode:
    """Guess the travel mode between two classified stops.

    The preferred mode follows the rule table; when it is not allowed in
    the season (see ``_SEASON_ALLOWED_MODES``), car is used instead.
    """
    if distance_m < _MAX_FOOT_M:
        preferred = TravelMode.FOOT
    elif StopClass.FERRY_TERMINAL in start and StopClass.FERRY_TERMINAL in destination:
        preferred = TravelMode.FERRY
    elif (
        StopClass.TRAIN_STATION in start
        and StopClass.TRAIN_STATION in destination
        and distance_m <= _MAX_TRAIN_M
    ):
        preferred = TravelMode.TRAIN
    elif (
        StopClass.BUS_STATION in start
        and StopClass.BUS_STATION in destination
        and StopClass.TRAIN_STATION not in start
        and StopClass.TRAIN_STATION not in destination
    ):
        preferred = TravelMode.BUS
    elif (
        StopClass.AIRPORT in start
        and StopClass.AIRPORT in destination
        and distance_m > _MIN_PLANE_M
    ):
        preferred = TravelMode.PLANE
    else:
        preferred = TravelMode.CAR
    if preferred in allowed:
        return preferred
    fallback = TravelMode.CAR if TravelMode.CAR in allowed else TravelMode.FOOT
    logger.info(
        "Season does not use %s; guessing %s instead", preferred.value, fallback.value
    )
    return fallback


def _overpass_classes(coordinate: Coordinate) -> frozenset[StopClass]:
    """Classify ``coordinate`` by querying the Overpass API mirrors."""
    query = _overpass_query(coordinate)
    last_error: Exception | None = None
    for _ in range(_MIRROR_ATTEMPTS):
        for mirror in _OVERPASS_MIRRORS:
            try:
                response = requests.post(
                    mirror,
                    data={"data": query},
                    headers={"User-Agent": _USER_AGENT},
                    timeout=_REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                data = _OverpassResponse.model_validate(response.json())
            except (requests.RequestException, ValidationError) as exc:
                last_error = exc
                continue
            if data.remark:
                last_error = GuessError(data.remark)
                continue
            return _classes_near(coordinate, data)
        time.sleep(_RETRY_SPACING_S)
    msg = f"all Overpass mirrors failed; last error: {last_error}"
    raise GuessError(msg)


def _classes_near(
    coordinate: Coordinate, data: _OverpassResponse
) -> frozenset[StopClass]:
    """Collect the infrastructure classes of ``data`` within one kilometre."""
    origin = (coordinate.longitude, coordinate.latitude)
    classes: set[StopClass] = set()
    for element in data.elements:
        stop_class = _element_class(element)
        if stop_class is None:
            continue
        if element.center is not None:
            position = (element.center.lon, element.center.lat)
        elif element.lat is not None and element.lon is not None:
            position = (element.lon, element.lat)
        else:
            continue
        if _haversine_m(origin, position) <= _CLASS_RADIUS_M:
            classes.add(stop_class)
    return frozenset(classes)


def _element_class(element: _OverpassElement) -> StopClass | None:
    """Map an Overpass element to the infrastructure class it proves, if any."""
    for tag, value in element.tags.items():
        if tag == "railway" and value in ("station", "halt"):
            return StopClass.TRAIN_STATION
        if tag == "amenity" and value == "bus_station":
            return StopClass.BUS_STATION
        if tag == "amenity" and value == "ferry_terminal":
            return StopClass.FERRY_TERMINAL
        if tag == "aeroway" and value in ("aerodrome", "terminal"):
            return StopClass.AIRPORT
    return None


def _overpass_query(coordinate: Coordinate) -> str:
    """Build the Overpass query for the infrastructure around ``coordinate``."""
    around = (
        f"around:{_CLASS_RADIUS_M:.0f},{coordinate.latitude},{coordinate.longitude}"
    )
    return (
        f"[out:json][timeout:{_OVERPASS_TIMEOUT_S:.0f}];("
        f'node({around})["railway"~"station|halt"];'
        f'node({around})["amenity"="bus_station"];'
        f'node({around})["amenity"="ferry_terminal"];'
        f'way({around})["aeroway"="aerodrome"];'
        f'node({around})["aeroway"="terminal"];'
        ");out tags center;"
    )


def _load_cache() -> dict[str, list[StopClass]]:
    """Read the stop classification cache, recreating it when corrupt."""
    try:
        return _cache_model.validate_json(_CACHE_FILE.read_text("utf-8"))
    except OSError, ValidationError:
        logger.warning("cache %s is unreadable; recreating it", _CACHE_FILE)
        return {}


def _save_cache(cache: dict[str, list[StopClass]]) -> None:
    """Persist the stop classification cache as pretty-printed JSON."""
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = _cache_model.dump_json(dict(sorted(cache.items())), indent=2).decode("utf-8")
    _CACHE_FILE.write_text(text + "\n", encoding="utf-8")


def _write_season(directory: Path, season: SeasonFile) -> None:
    """Write ``season`` back into its JSON file when its content changed."""
    path = directory / f"{season.slug}.json"
    text = season.model_dump_json(indent=2) + "\n"
    if path.read_text("utf-8") != text:
        path.write_text(text, encoding="utf-8")
        logger.info("Wrote %s", path)


def _log_season(
    season: SeasonFile,
    mode_counts: Counter[TravelMode],
    skipped: int,
    sources: dict[str, _ClassSource],
) -> None:
    """Log the per-season summary of guessed legs and classifications."""
    legs = ", ".join(
        f"{mode.value}={count}" for mode, count in sorted(mode_counts.items())
    )
    if not legs:
        legs = "none"
    source_counts = Counter(
        sources[_cache_key(stop.coordinate)]
        for journey in season.journeys
        for stop in journey.stops
    )
    source_text = ", ".join(
        f"{source.value}={count}" for source, count in sorted(source_counts.items())
    )
    logger.info(
        "Season %d (%s): guessed legs: %s; skipped %d stop pairs (<%.0f m);"
        " classifications: %s",
        season.season,
        season.name,
        legs,
        skipped,
        _MIN_LEG_M,
        source_text,
    )


def _cache_key(coordinate: Coordinate) -> str:
    """Build the cache key of a coordinate from its rounded lon/lat."""
    return f"{coordinate.longitude:.6f},{coordinate.latitude:.6f}"


def _haversine_m(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Great-circle distance in metres between two lon/lat points."""
    lon1, lat1, lon2, lat2 = (
        math.radians(p1[0]),
        math.radians(p1[1]),
        math.radians(p2[0]),
        math.radians(p2[1]),
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))
