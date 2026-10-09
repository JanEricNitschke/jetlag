"""Fetch real-world route geometry for travel legs, one source per mode.

Sources per mode
---------------
car
    OSRM public demo server (``https://router.project-osrm.org``) with the
    ``driving`` profile.
bus
    OpenStreetMap bus lines (``route=bus`` relations) via the Overpass API:
    relations that run near the corridor between the endpoints and whose
    bounding box comes near both endpoints are fetched with their member-way
    geometry, and the leg is stitched along the best matching relation.
    Falls back to the OSRM ``driving`` profile with a warning when no bus
    relation connects the endpoints.
bike, foot
    FOSSGIS OSRM instances (``https://routing.openstreetmap.de/routed-bike``
    and ``.../routed-foot``), which run real ``bike`` and ``foot`` profiles.
    The public demo server ignores the profile segment of the URL and always
    answers with driving geometry, so it is not usable for these modes.
ferry
    OpenStreetMap ferry ways (``route=ferry``) via the Overpass API near the
    corridor between the endpoints, stitched into a path with a
    shortest-path search over the ferry-way graph. Falls back to a straight
    line with a warning when no ferry way connects the endpoints.
train
    OpenStreetMap railways (``railway=rail``) via the Overpass API near the
    straight-line corridor between the endpoints, stitched with the same
    shortest-path search. Best-effort prototype: the result follows *some*
    rail line between the endpoints, not necessarily the line actually used.
    When ``fetch_geometry`` receives a ``note`` hint, ``route=train``
    relations whose ``ref`` or ``name`` is contained in the note
    (case-insensitive, e.g. a note of ``"Took the のぞみ to Osaka"`` matches
    the ``のぞみ`` Shinkansen relations) are preferred and the leg is
    stitched along the matched relation instead; falls back to the generic
    railway method with a warning when no matching relation connects the
    endpoints.
plane
    Great-circle spherical interpolation between the endpoints. When the
    ``note`` contains a flight designator, the actually flown track is
    scraped from FlightAware instead, by descending the resolution chain
    described in ``fetch_geometry``: an explicit designator constructs a
    history permalink directly; otherwise the flight number's live page
    (``https://www.flightaware.com/live/flight/...``) is fetched and the
    track taken from the inline ``trackpollBootstrap`` JSON or resolved
    via the page's activity-log permalinks. A dated designator whose
    flight is not on the first activity-log page is additionally looked
    up in the ``logpoll.rvt`` AJAX endpoint that the page's "show more"
    UI uses, but anonymous access only ever exposes about two weeks of
    activity-log history, so years-old flights are only reachable via
    the explicit designator form. When no track of the flight number
    can be found, a dated designator prefers a representative same-route
    flight over the most recent finished flight of the same number,
    because a current flight of a number may serve a completely
    different route; an undated designator keeps the opposite
    preference. A representative flight of the same route is resolved
    as follows: the route airports come from the designator's
    ``ORIG-DEST`` or, when absent, from OpenStreetMap
    ``aeroway=aerodrome`` elements (nodes, ways, or relations) within
    5 km of the leg's endpoints (``icao``/``iata`` tags queried via
    Overpass), and the current and recent flights between them are
    listed from
    ``https://www.flightaware.com/live/findflight?origin=...&destination=...``.
    That page does not render flight links server-side; its rows live in
    an inline ``FA.findflight.resultsContent`` JSON array whose
    ``flightIdent`` HTML anchors each row's concrete flight instance at
    ``/live/flight/id/...``. The concrete instance link is used because
    the bare ``/live/flight/{ident}`` page can resolve to a different
    leg of the same flight number (observed: the reverse route), and the
    track of the first listed landed or airborne same-route flight wins.
    The result is representative geometry, not the leg's actually flown
    path, which is called out in a warning. This is HTML scraping
    without an official API and breaks whenever FlightAware changes its
    page layout; on any failure (blocked, restructured page, unknown
    flight) the great-circle path is returned with a warning. The
    OpenSky Network (https://opensky-network.org/) remains a possible
    API-based alternative for actually flown paths.
"""

from __future__ import annotations

import heapq
import itertools
import json
import math
import re
import warnings
from typing import NamedTuple

import requests
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from pydantic.alias_generators import to_camel

from jetlag_maps.format import Coordinate, TravelMode

_MIN_POLYLINE_NODES = 2
_MIN_CORRIDOR_SAMPLES = 3
_FERRY_CORRIDOR_SPACING_M = 30_000.0
_TRAIN_CORRIDOR_SPACING_M = 10_000.0

_OSRM_DEMO = "https://router.project-osrm.org"
_OSRM_FOSSGIS = "https://routing.openstreetmap.de/routed-{profile}"
_OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
_USER_AGENT = "jetlag-maps/0.1 (route geometry prototype)"
_REQUEST_TIMEOUT = 30.0
_OVERPASS_TIMEOUT = 60.0
_PLANE_POINTS = 32
_ANTIPODE_EPSILON = 1e-9
_FERRY_SEARCH_RADIUS_M = 25_000.0
_FERRY_MAX_SNAP_M = 30_000.0
_FERRY_BRIDGE_M = 2_500.0
_TRAIN_SEARCH_RADIUS_M = 10_000.0
_TRAIN_MAX_SNAP_M = 10_000.0
_TRAIN_BRIDGE_M = 1_000.0
_BUS_SEARCH_RADIUS_M = 25_000.0
_BUS_CORRIDOR_SPACING_M = 50_000.0
_BUS_MAX_SNAP_M = 30_000.0
_BUS_BRIDGE_M = 1_000.0
_TRAIN_LINE_SEARCH_RADIUS_M = 25_000.0
_TRAIN_LINE_CORRIDOR_SPACING_M = 50_000.0
_TRAIN_LINE_MAX_SNAP_M = 30_000.0
_TRAIN_LINE_BRIDGE_M = 1_000.0
_MAX_RELATION_CANDIDATES = 8
_M_PER_DEGREE = 111_000.0

_FLIGHTAWARE_BASE = "https://www.flightaware.com/live/flight"
_FLIGHTAWARE_FINDFLIGHT = "https://www.flightaware.com/live/findflight"
_FLIGHTAWARE_LOGPOLL = "https://www.flightaware.com/ajax/logpoll.rvt"
_MAX_FINISHED_ATTEMPTS = 3
_MAX_ROUTE_ATTEMPTS = 3
_AIRPORT_QUERY_RADIUS_M = 5_000.0
_FLIGHTAWARE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
    ),
    "Accept-Language": "en-US,en;q=0.5",
}
_TRACKPOLL_RE = re.compile(r"var trackpollBootstrap = ")
_TRACKPOLL_TOKEN_RE = re.compile(r'trackpollGlobals\s*=\s*\{"TOKEN":"([^"]+)"')
_PERMALINK_DATE_RE = re.compile(r"/history/(\d{8})/")
_FINDFLIGHT_RESULTS_RE = re.compile(
    r"FA\.findflight\.resultsContent\s*=\s*(\[.*?\]);", re.DOTALL
)
_FINDFLIGHT_IDENT_RE = re.compile(r">\s*([A-Z0-9]+)\s*</a>")
_FINDFLIGHT_LINK_RE = re.compile(r'href="([^"]+)"')
_FLIGHT_NOTE_RE = re.compile(
    r"\b(?P<ident>[A-Z]{2,3}\d{1,4})"
    r"(?:@(?P<date>\d{4}-\d{2}-\d{2}|\d{8}))?"
    r"(?:\s+(?P<time>\d{2}:?\d{2})Z)?"
    r"(?:\s+(?P<origin>[A-Z]{3,4})-(?P<destination>[A-Z]{3,4}))?"
)


class RouteError(RuntimeError):
    """Base class for route-geometry lookup failures."""


class RouteNotFoundError(RouteError):
    """No usable route geometry could be found for the given leg."""


class _LonLat(BaseModel):
    """A latitude/longitude point as returned by the Overpass API."""

    lat: float
    lon: float


class _OsrmGeometry(BaseModel):
    """A GeoJSON geometry of an OSRM route (``geometries=geojson``)."""

    coordinates: list[tuple[float, float]]


class _OsrmRoute(BaseModel):
    """A single route of an OSRM response."""

    distance: float
    geometry: _OsrmGeometry


class _OsrmResponse(BaseModel):
    """The top level of an OSRM route response."""

    code: str
    routes: list[_OsrmRoute] = []


class _OverpassWaysResponse(BaseModel):
    """An Overpass response to a way query with ``out geom``."""

    elements: list[_LonLatGeometryElement] = []


class _LonLatGeometryElement(BaseModel):
    """An Overpass element carrying a member-way ``geometry`` of points."""

    geometry: list[_LonLat] = []


class _OverpassBbox(BaseModel):
    """The bounding box of an Overpass relation (``out tags bb``)."""

    minlat: float
    minlon: float
    maxlat: float
    maxlon: float


class _OverpassTags(BaseModel):
    """The tags of an Overpass route relation that matter for matching."""

    ref: str = ""
    name: str = ""


class _OverpassRelationSummary(BaseModel):
    """An Overpass relation summary (``out tags bb``)."""

    id: int
    bounds: _OverpassBbox | None = None
    tags: _OverpassTags | None = None


class _OverpassRelationsResponse(BaseModel):
    """An Overpass response to a relation query with ``out tags bb``."""

    elements: list[_OverpassRelationSummary] = []


class _OverpassRelationGeometry(BaseModel):
    """An Overpass relation with member geometries (``out geom``)."""

    members: list[_LonLatGeometryElement] = []


class _OverpassRelationGeometryResponse(BaseModel):
    """An Overpass response to a relation query with ``out geom``."""

    elements: list[_OverpassRelationGeometry] = []


class _TrackPoint(BaseModel):
    """A single FlightAware track position (``coord`` is lon/lat)."""

    coord: tuple[float, float]
    timestamp: int = 0


class _ActivityLogFlight(BaseModel):
    """An activity-log entry of a FlightAware flight."""

    model_config = ConfigDict(alias_generator=to_camel)

    perma_link: str = ""


class _ActivityLog(BaseModel):
    """The activity log of a FlightAware flight page."""

    flights: list[_ActivityLogFlight] = []
    additional_log_rows_available: bool = False


class _TrackFlight(BaseModel):
    """A FlightAware flight entry of the ``trackpollBootstrap`` blob."""

    model_config = ConfigDict(alias_generator=to_camel)

    track: list[_TrackPoint] | None = None
    activity_log: _ActivityLog | None = None


class _TrackpollBootstrap(BaseModel):
    """The ``trackpollBootstrap`` JSON blob embedded in FlightAware pages."""

    flights: dict[str, _TrackFlight] = {}


class _OverpassAirportTags(BaseModel):
    """The ``iata``/``icao`` tags of an OSM aerodrome way."""

    iata: str = ""
    icao: str = ""


class _OverpassAirportElement(BaseModel):
    """An Overpass aerodrome element from an ``out tags center`` query."""

    lat: float | None = None
    lon: float | None = None
    center: _LonLat | None = None
    tags: _OverpassAirportTags | None = None

    @property
    def point(self) -> _LonLat | None:
        """The element position from ``center`` (ways) or ``lat``/``lon``."""
        if self.center is not None:
            return self.center
        if self.lat is not None and self.lon is not None:
            return _LonLat(lat=self.lat, lon=self.lon)
        return None


class _OverpassAirportsResponse(BaseModel):
    """An Overpass response to an aerodrome query with ``out tags center``."""

    elements: list[_OverpassAirportElement] = []


class _FindflightResult(BaseModel):
    """A route-row of the inline ``FA.findflight.resultsContent`` array."""

    model_config = ConfigDict(alias_generator=to_camel)

    origin: str = ""
    destination: str = ""
    flight_ident: str = ""
    flight_status: str = ""


class _RouteRelation(NamedTuple):
    """A `route` relation from Overpass with the fields needed for matching."""

    id: int
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    ref: str
    name: str


class _FlightDesignator(NamedTuple):
    """A flight designator parsed from a leg note.

    ``date`` is ``YYYYMMDD``, ``time`` is departure ``HHMM`` Zulu, and
    ``origin``/``destination`` are ICAO airport codes.
    """

    ident: str
    date: str | None = None
    time: str | None = None
    origin: str | None = None
    destination: str | None = None

    @property
    def is_explicit(self) -> bool:
        """Whether all pieces of a full history permalink are present."""
        return None not in (self.date, self.time, self.origin, self.destination)


class _RouteFlight(NamedTuple):
    """A current FlightAware flight on a requested route."""

    ident: str
    link: str


class _RepresentativeTrack(NamedTuple):
    """A same-route flight's track standing in for another flight."""

    ident: str
    origin: str
    destination: str
    track: list[Coordinate]


def fetch_geometry(
    mode: TravelMode,
    start: Coordinate,
    end: Coordinate,
    note: str = "",
) -> list[Coordinate]:
    """Fetch polyline geometry for a leg with ``mode`` from ``start`` to ``end``.

    Parameters
    ----------
    mode
        Travel mode of the leg; selects the geometry source.
    start
        Start coordinate of the leg (longitude/latitude).
    end
        End coordinate of the leg (longitude/latitude).
    note
        Free-text note of the leg. For ``train``, relations whose ``ref``
        or ``name`` is contained in ``note`` (case-insensitive, e.g. a
        note of ``"Took the のぞみ to Osaka"`` matches the ``のぞみ``
        Shinkansen relations) are preferred over the generic railway
        corridor. For ``plane``, a flight designator in ``note`` selects
        the actually flown track scraped from FlightAware. The accepted
        grammar is ``IDENT[@YYYY-MM-DD][ HH:MMZ][ ORIG-DEST]`` where
        ``IDENT`` is an airline code plus 1-4 digits (e.g. ``"UAL1"``),
        the optional date pins a specific day, the optional Zulu
        departure time (colon optional) and the optional
        ``ORIG-DEST`` ICAO pair complete a fully explicit designator
        (e.g. ``"UAL1@2026-09-27 05:50Z KSFO-WSSS"``) whose history
        permalink can be constructed directly, reaching flights far
        beyond the ~two weeks of activity-log history that anonymous
        FlightAware access exposes. When no track of the named flight
        can be found, a representative current flight on the same route
        is used with a warning; a dated designator prefers it over the
        most recent finished flight of the same number, an undated one
        only falls back to it after that. Ignored for other modes.

    Returns
    -------
    list[Coordinate]
        Ordered polyline including snapped start and end coordinates.

    Raises
    ------
    RouteNotFoundError
        If no geometry could be retrieved for the leg.
    """
    match mode:
        case TravelMode.CAR:
            return _fetch_osrm(_OSRM_DEMO, "driving", start, end)
        case TravelMode.BUS:
            return _fetch_bus(start, end)
        case TravelMode.BIKE:
            return _fetch_osrm(_OSRM_FOSSGIS.format(profile="bike"), "bike", start, end)
        case TravelMode.FOOT:
            return _fetch_osrm(_OSRM_FOSSGIS.format(profile="foot"), "foot", start, end)
        case TravelMode.PLANE:
            return _fetch_plane(start, end, note)
        case TravelMode.FERRY:
            return _fetch_ferry(start, end)
        case TravelMode.TRAIN:
            return _fetch_train(start, end, note)


def _fetch_osrm(
    base_url: str, profile: str, start: Coordinate, end: Coordinate
) -> list[Coordinate]:
    url = (
        f"{base_url}/route/v1/{profile}/"
        f"{start.longitude},{start.latitude};{end.longitude},{end.latitude}"
    )
    try:
        response = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = _OsrmResponse.model_validate(response.json())
    except (requests.RequestException, ValidationError) as exc:
        msg = f"OSRM request for profile {profile!r} failed: {exc}"
        raise RouteNotFoundError(msg) from exc
    if data.code != "Ok" or not data.routes:
        msg = f"OSRM found no {profile} route from {start} to {end} (code: {data.code})"
        raise RouteNotFoundError(msg)
    coordinates = data.routes[0].geometry.coordinates
    if not coordinates:
        msg = f"OSRM returned an empty geometry for profile {profile!r}"
        raise RouteNotFoundError(msg)
    return [Coordinate(longitude=lon, latitude=lat) for lon, lat in coordinates]


def _fetch_ferry(start: Coordinate, end: Coordinate) -> list[Coordinate]:
    polylines = _overpass_corridor_ways(
        'route="ferry"', start, end, _FERRY_SEARCH_RADIUS_M, _FERRY_CORRIDOR_SPACING_M
    )
    path = _shortest_graph_path(
        polylines, start, end, _FERRY_MAX_SNAP_M, _FERRY_BRIDGE_M
    )
    if path is None:
        warnings.warn(
            f"no OSM ferry route found between {start} and {end}; "
            "falling back to a straight line",
            stacklevel=2,
        )
        return _straight_line(start, end)
    return _snap_to_path(path, start, end)


def _fetch_bus(start: Coordinate, end: Coordinate) -> list[Coordinate]:
    path = _relation_line_path(
        'route="bus"',
        start,
        end,
        _BUS_SEARCH_RADIUS_M,
        _BUS_CORRIDOR_SPACING_M,
        _BUS_MAX_SNAP_M,
        _BUS_BRIDGE_M,
    )
    if path is None:
        warnings.warn(
            f"no OSM bus route connects {start} and {end}; "
            "falling back to OSRM driving geometry",
            stacklevel=2,
        )
        return _fetch_osrm(_OSRM_DEMO, "driving", start, end)
    return _snap_to_path(path, start, end)


def _fetch_train(
    start: Coordinate, end: Coordinate, note: str = ""
) -> list[Coordinate]:
    if note:
        path = _relation_line_path(
            'route="train"',
            start,
            end,
            _TRAIN_LINE_SEARCH_RADIUS_M,
            _TRAIN_LINE_CORRIDOR_SPACING_M,
            _TRAIN_LINE_MAX_SNAP_M,
            _TRAIN_LINE_BRIDGE_M,
            note,
        )
        if path is not None:
            return _snap_to_path(path, start, end)
        warnings.warn(
            f"no OSM train route matching {note!r} connects {start} and {end}; "
            "falling back to generic railway geometry",
            stacklevel=2,
        )
    polylines = _overpass_corridor_ways(
        'railway="rail"', start, end, _TRAIN_SEARCH_RADIUS_M, _TRAIN_CORRIDOR_SPACING_M
    )
    path = _shortest_graph_path(
        polylines, start, end, _TRAIN_MAX_SNAP_M, _TRAIN_BRIDGE_M
    )
    if path is None:
        msg = f"no OSM railway line connects {start} and {end}"
        raise RouteNotFoundError(msg)
    return _snap_to_path(path, start, end)


def _fetch_plane(start: Coordinate, end: Coordinate, note: str) -> list[Coordinate]:
    designator = _parse_flight_designator(note)
    if designator is None:
        return _great_circle(start, end)
    try:
        return _fetch_flightaware_track(designator, start, end)
    except RouteError as exc:
        warnings.warn(
            f"could not scrape a FlightAware track for flight {designator.ident!r}"
            f" ({exc}); using a great-circle path",
            stacklevel=2,
        )
        return _great_circle(start, end)


def _great_circle(start: Coordinate, end: Coordinate) -> list[Coordinate]:
    return [
        Coordinate(longitude=lon, latitude=lat)
        for lon, lat in _sphere_points(start, end, _PLANE_POINTS)
    ]


def _parse_flight_designator(note: str) -> _FlightDesignator | None:
    """Extract a flight designator with optional date, time, and route."""
    match = _FLIGHT_NOTE_RE.search(note.upper())
    if match is None:
        return None
    date = match.group("date")
    time = match.group("time")
    return _FlightDesignator(
        ident=match.group("ident"),
        date=date.replace("-", "") if date else None,
        time=time.replace(":", "") if time else None,
        origin=match.group("origin"),
        destination=match.group("destination"),
    )


def _fetch_flightaware_track(
    designator: _FlightDesignator, start: Coordinate, end: Coordinate
) -> list[Coordinate]:
    if designator.is_explicit:
        permalink = (
            f"/live/flight/{designator.ident}/history/{designator.date}/"
            f"{designator.time}Z/{designator.origin}/{designator.destination}"
        )
        try:
            return _fetch_permalink_track(permalink)
        except RouteError:
            pass
    bootstrap: _TrackpollBootstrap | None = None
    try:
        page = _flightaware_get(f"{_FLIGHTAWARE_BASE}/{designator.ident}")
        bootstrap = _parse_trackpoll(page)
        if designator.date is None:
            track = _extract_track(bootstrap)
            if track is not None:
                return track
        link = _resolve_history_link(bootstrap, page, designator.date)
        return _fetch_permalink_track(link)
    except RouteError:
        pass
    if designator.date is None and bootstrap is not None:
        fallback = _recent_finished_track(bootstrap)
        if fallback is not None:
            warnings.warn(
                f"no exact FlightAware match for {designator.ident!r};"
                " using the most recent finished flight of that number",
                stacklevel=3,
            )
            return fallback
    representative = _same_route_track(designator, start, end)
    if representative is not None:
        warnings.warn(
            f"no exact FlightAware match for {designator.ident!r}"
            f"{f' on {designator.date}' if designator.date else ''};"
            f" using a representative same-route flight {representative.ident!r}"
            f" ({representative.origin}->{representative.destination})"
            " instead; its geometry is representative, not the actually"
            " flown path",
            stacklevel=3,
        )
        return representative.track
    if designator.date is not None and bootstrap is not None:
        fallback = _recent_finished_track(bootstrap)
        if fallback is not None:
            warnings.warn(
                f"no exact FlightAware match for {designator.ident!r}"
                f" on {designator.date};"
                " using the most recent finished flight of that number",
                stacklevel=3,
            )
            return fallback
    msg = f"no usable FlightAware track for {designator.ident!r}"
    raise RouteNotFoundError(msg)


def _recent_finished_track(
    bootstrap: _TrackpollBootstrap,
) -> list[Coordinate] | None:
    for link in _activity_log_permalinks(bootstrap)[:_MAX_FINISHED_ATTEMPTS]:
        try:
            return _fetch_permalink_track(link)
        except RouteError:
            continue
    return None


def _fetch_permalink_track(link: str) -> list[Coordinate]:
    """Fetch the flown track of the FlightAware flight behind ``link``."""
    track = _extract_track(_parse_trackpoll(_flightaware_get(_permalink_url(link))))
    if track is None:
        msg = f"the FlightAware flight at {link} has no usable flown track"
        raise RouteNotFoundError(msg)
    return track


def _permalink_url(link: str) -> str:
    return link if link.startswith("http") else f"https://www.flightaware.com{link}"


def _flightaware_get(url: str) -> str:
    response = requests.get(url, headers=_FLIGHTAWARE_HEADERS, timeout=_REQUEST_TIMEOUT)
    if response.status_code != requests.codes.ok:
        msg = f"FlightAware returned HTTP {response.status_code} for {url}"
        raise RouteNotFoundError(msg)
    return response.text


def _parse_trackpoll(html: str) -> _TrackpollBootstrap:
    match = _TRACKPOLL_RE.search(html)
    if match is None:
        msg = "the FlightAware page layout is not as expected (no trackpollBootstrap)"
        raise RouteNotFoundError(msg)
    blob = html[match.end() : html.find("</script>", match.end())].rstrip().rstrip(";")
    try:
        return _TrackpollBootstrap.model_validate(json.loads(blob))
    except json.JSONDecodeError as exc:
        msg = "the FlightAware trackpoll data is malformed"
        raise RouteNotFoundError(msg) from exc


def _extract_track(bootstrap: _TrackpollBootstrap) -> list[Coordinate] | None:
    for flight in bootstrap.flights.values():
        if flight.track:
            return [
                Coordinate(longitude=point.coord[0], latitude=point.coord[1])
                for point in flight.track
            ]
    return None


def _resolve_history_link(
    bootstrap: _TrackpollBootstrap, page: str, when: str | None
) -> str:
    permalinks = _activity_log_permalinks(bootstrap)
    if when is not None and _more_log_rows_available(bootstrap):
        token = _trackpoll_token(page)
        if token is not None:
            permalinks = [
                *_activity_log_permalinks(_logpoll_bootstrap(token)),
                *permalinks,
            ]
    if when is not None:
        for permalink in permalinks:
            date = _PERMALINK_DATE_RE.search(permalink)
            if date is not None and date.group(1) == when:
                return permalink
        msg = f"no FlightAware flight on {when[:4]}-{when[4:6]}-{when[6:]}"
        raise RouteNotFoundError(msg)
    if permalinks:
        return permalinks[0]
    msg = "the FlightAware page lists no recent flights of this number"
    raise RouteNotFoundError(msg)


def _activity_log_permalinks(bootstrap: _TrackpollBootstrap) -> list[str]:
    return [
        entry.perma_link
        for flight in bootstrap.flights.values()
        if flight.activity_log is not None
        for entry in flight.activity_log.flights
        if entry.perma_link
    ]


def _more_log_rows_available(bootstrap: _TrackpollBootstrap) -> bool:
    return any(
        flight.activity_log is not None
        and flight.activity_log.additional_log_rows_available
        for flight in bootstrap.flights.values()
    )


def _trackpoll_token(page: str) -> str | None:
    match = _TRACKPOLL_TOKEN_RE.search(page)
    return match.group(1) if match is not None else None


def _logpoll_bootstrap(token: str) -> _TrackpollBootstrap:
    """Fetch the extended activity log behind the page's "show more" UI."""
    text = _flightaware_get(f"{_FLIGHTAWARE_LOGPOLL}?token={token}")
    try:
        return _TrackpollBootstrap.model_validate(json.loads(text))
    except json.JSONDecodeError as exc:
        msg = "the FlightAware logpoll data is malformed"
        raise RouteNotFoundError(msg) from exc


def _same_route_track(
    designator: _FlightDesignator, start: Coordinate, end: Coordinate
) -> _RepresentativeTrack | None:
    """Scrape the track of a current flight on the designator's route.

    Parameters
    ----------
    designator
        The parsed flight designator whose own track was not usable.
    start
        Start coordinate of the leg, used to infer the origin airport.
    end
        End coordinate of the leg, used to infer the destination airport.

    Returns
    -------
    _RepresentativeTrack | None
        The first usable same-route track, or ``None`` when the route
        airports cannot be determined or no listed flight has a track.
    """
    airports = _route_airports(designator, start, end)
    if airports is None:
        return None
    origin, destination = airports
    try:
        page = _flightaware_get(
            f"{_FLIGHTAWARE_FINDFLIGHT}?origin={origin}&destination={destination}"
        )
        flights = _route_flights(page)
    except RouteError:
        return None
    for flight in flights[:_MAX_ROUTE_ATTEMPTS]:
        try:
            track = _extract_track(
                _parse_trackpoll(_flightaware_get(_permalink_url(flight.link)))
            )
        except RouteError:
            continue
        if track is not None:
            return _RepresentativeTrack(
                ident=flight.ident,
                origin=origin,
                destination=destination,
                track=track,
            )
    return None


def _route_airports(
    designator: _FlightDesignator, start: Coordinate, end: Coordinate
) -> tuple[str, str] | None:
    if designator.origin is not None and designator.destination is not None:
        return designator.origin, designator.destination
    return _overpass_airport_codes(start, end)


def _overpass_airport_codes(
    start: Coordinate, end: Coordinate
) -> tuple[str, str] | None:
    clauses = ";".join(
        f'nwr["aeroway"="aerodrome"](around:{_AIRPORT_QUERY_RADIUS_M:.0f},'
        f"{point.latitude},{point.longitude})"
        for point in (start, end)
    )
    query = f"[out:json][timeout:25];({clauses};);out tags center;"
    try:
        data = _overpass_request(query, _OverpassAirportsResponse)
    except RouteError:
        return None
    start_code = _nearest_airport_code(data.elements, start)
    end_code = _nearest_airport_code(data.elements, end)
    if start_code is None or end_code is None:
        return None
    return start_code, end_code


def _nearest_airport_code(
    elements: list[_OverpassAirportElement], point: Coordinate
) -> str | None:
    best_distance = math.inf
    best_code: str | None = None
    for element in elements:
        position = element.point
        if position is None or element.tags is None:
            continue
        code = element.tags.icao or element.tags.iata
        if not code:
            continue
        distance = _haversine_m(
            (point.longitude, point.latitude), (position.lon, position.lat)
        )
        if distance < best_distance:
            best_distance = distance
            best_code = code
    return best_code


def _route_flights(page: str) -> list[_RouteFlight]:
    match = _FINDFLIGHT_RESULTS_RE.search(page)
    if match is None:
        msg = "the FlightAware route page has no findflight results"
        raise RouteNotFoundError(msg)
    try:
        results = TypeAdapter(list[_FindflightResult]).validate_json(match.group(1))
    except ValidationError as exc:
        msg = "the FlightAware findflight results are malformed"
        raise RouteNotFoundError(msg) from exc
    flights: list[_RouteFlight] = []
    seen: set[str] = set()
    for result in sorted(results, key=_route_flight_rank):
        ident_match = _FINDFLIGHT_IDENT_RE.search(result.flight_ident)
        link_match = _FINDFLIGHT_LINK_RE.search(result.flight_ident)
        if ident_match is None or link_match is None:
            continue
        ident = ident_match.group(1)
        if ident in seen:
            continue
        seen.add(ident)
        flights.append(_RouteFlight(ident=ident, link=link_match.group(1)))
    return flights


def _route_flight_rank(result: _FindflightResult) -> int:
    status = result.flight_status.casefold()
    if "arrived" in status:
        return 0
    if "en route" in status:
        return 1
    return 2


def _corridor_samples(
    start: Coordinate, end: Coordinate, spacing_m: float
) -> list[tuple[float, float]]:
    distance_m = _haversine_m(
        (start.longitude, start.latitude), (end.longitude, end.latitude)
    )
    samples = max(_MIN_CORRIDOR_SAMPLES, int(distance_m / spacing_m) + 1)
    return _sphere_points(start, end, samples)


def _overpass_request[T: BaseModel](query: str, model: type[T]) -> T:
    last_error: Exception | None = None
    for mirror in _OVERPASS_MIRRORS:
        try:
            response = requests.post(
                mirror,
                data={"data": query},
                headers={"User-Agent": _USER_AGENT},
                timeout=_OVERPASS_TIMEOUT,
            )
            response.raise_for_status()
            return model.model_validate(response.json())
        except (requests.RequestException, ValidationError) as exc:
            last_error = exc
    msg = f"all Overpass mirrors failed; last error: {last_error}"
    raise RouteNotFoundError(msg)


def _overpass_corridor_ways(
    tag_filter: str,
    start: Coordinate,
    end: Coordinate,
    radius_m: float,
    spacing_m: float,
) -> list[list[tuple[float, float]]]:
    corridor = _corridor_samples(start, end, spacing_m)
    clauses = ";".join(
        f"way[{tag_filter}](around:{radius_m:.0f},{lat},{lon})" for lon, lat in corridor
    )
    query = f"[out:json][timeout:50];({clauses};);out geom;"
    data = _overpass_request(query, _OverpassWaysResponse)
    polylines: list[list[tuple[float, float]]] = []
    for element in data.elements:
        polyline = _polyline_from_geometry(element.geometry)
        if polyline is not None:
            polylines.append(polyline)
    return polylines


def _corridor_relations(
    tag_filter: str,
    start: Coordinate,
    end: Coordinate,
    radius_m: float,
    spacing_m: float,
) -> list[_RouteRelation]:
    corridor = _corridor_samples(start, end, spacing_m)
    clauses = ";".join(
        f"relation[{tag_filter}](around:{radius_m:.0f},{lat},{lon})"
        for lon, lat in corridor
    )
    query = f"[out:json][timeout:50];({clauses};);out tags bb;"
    data = _overpass_request(query, _OverpassRelationsResponse)
    relations: list[_RouteRelation] = []
    for element in data.elements:
        if element.bounds is None or element.tags is None:
            continue
        relations.append(
            _RouteRelation(
                id=element.id,
                min_lat=element.bounds.minlat,
                min_lon=element.bounds.minlon,
                max_lat=element.bounds.maxlat,
                max_lon=element.bounds.maxlon,
                ref=element.tags.ref,
                name=element.tags.name,
            )
        )
    return relations


def _relation_polylines(relation_id: int) -> list[list[tuple[float, float]]]:
    query = f"[out:json][timeout:50];relation({relation_id});out geom;"
    data = _overpass_request(query, _OverpassRelationGeometryResponse)
    polylines: list[list[tuple[float, float]]] = []
    for element in data.elements:
        for member in element.members:
            polyline = _polyline_from_geometry(member.geometry)
            if polyline is not None:
                polylines.append(polyline)
    return polylines


def _polyline_from_geometry(
    geometry: list[_LonLat],
) -> list[tuple[float, float]] | None:
    polyline = [(point.lon, point.lat) for point in geometry]
    if len(polyline) < _MIN_POLYLINE_NODES:
        return None
    return polyline


def _relation_line_path(
    tag_filter: str,
    start: Coordinate,
    end: Coordinate,
    radius_m: float,
    spacing_m: float,
    max_snap_m: float,
    bridge_m: float,
    note: str = "",
) -> list[tuple[float, float]] | None:
    relations = _corridor_relations(tag_filter, start, end, radius_m, spacing_m)
    candidates = [
        relation
        for relation in relations
        if (not note or _relation_matches_note(relation, note))
        and _relation_bbox_distance_m(relation, start) < max_snap_m
        and _relation_bbox_distance_m(relation, end) < max_snap_m
    ]
    candidates.sort(key=_relation_bbox_area)
    for relation in candidates[:_MAX_RELATION_CANDIDATES]:
        path = _shortest_graph_path(
            _relation_polylines(relation.id), start, end, max_snap_m, bridge_m
        )
        if path is not None:
            return path
    return None


def _relation_matches_note(relation: _RouteRelation, note: str) -> bool:
    haystack = note.casefold()
    return (bool(relation.ref) and relation.ref.casefold() in haystack) or (
        bool(relation.name) and relation.name.casefold() in haystack
    )


def _relation_bbox_distance_m(relation: _RouteRelation, point: Coordinate) -> float:
    dx = max(
        relation.min_lon - point.longitude, 0.0, point.longitude - relation.max_lon
    )
    dy = max(relation.min_lat - point.latitude, 0.0, point.latitude - relation.max_lat)
    return math.hypot(dx * math.cos(math.radians(point.latitude)), dy) * _M_PER_DEGREE


def _relation_bbox_area(relation: _RouteRelation) -> float:
    return (
        (relation.max_lat - relation.min_lat)
        * (relation.max_lon - relation.min_lon)
        * math.cos(math.radians((relation.min_lat + relation.max_lat) / 2.0))
    )


def _shortest_graph_path(
    polylines: list[list[tuple[float, float]]],
    start: Coordinate,
    end: Coordinate,
    max_snap_m: float,
    bridge_m: float,
) -> list[tuple[float, float]] | None:
    if not polylines:
        return None
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float]]] = {}
    for polyline in polylines:
        nodes = [(_round7(lon), _round7(lat)) for lon, lat in polyline]
        for node, next_node in itertools.pairwise(nodes):
            weight = _haversine_m(node, next_node)
            graph.setdefault(node, []).append((next_node, weight))
            graph.setdefault(next_node, []).append((node, weight))
    if not graph:
        return None
    if bridge_m > 0.0:
        _bridge_endpoints(graph, bridge_m)
    start_node = _nearest_node(graph, (start.longitude, start.latitude), max_snap_m)
    end_node = _nearest_node(graph, (end.longitude, end.latitude), max_snap_m)
    if start_node is None or end_node is None:
        return None
    return _dijkstra(graph, start_node, end_node)


def _bridge_endpoints(
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    bridge_m: float,
) -> None:
    endpoints = [node for node, neighbors in graph.items() if len(neighbors) == 1]
    cell_degrees = bridge_m / 111_000.0
    grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for node in endpoints:
        cell = (
            math.floor(node[0] / cell_degrees),
            math.floor(node[1] / cell_degrees),
        )
        grid.setdefault(cell, []).append(node)
    bridged: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for (cell_x, cell_y), nodes in grid.items():
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for other in grid.get((cell_x + offset_x, cell_y + offset_y), []):
                    for node in nodes:
                        if node == other:
                            continue
                        weight = _haversine_m(node, other)
                        if weight > bridge_m:
                            continue
                        pair = (node, other) if node < other else (other, node)
                        if pair in bridged:
                            continue
                        bridged.add(pair)
                        graph[node].append((other, weight))
                        graph[other].append((node, weight))


def _nearest_node(
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    origin: tuple[float, float],
    max_distance_m: float,
) -> tuple[float, float] | None:
    best: tuple[float, float] | None = None
    best_distance = max_distance_m
    for node in graph:
        distance = _haversine_m(origin, node)
        if distance < best_distance:
            best = node
            best_distance = distance
    return best


def _dijkstra(
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    start_node: tuple[float, float],
    end_node: tuple[float, float],
) -> list[tuple[float, float]] | None:
    distances: dict[tuple[float, float], float] = {start_node: 0.0}
    previous: dict[tuple[float, float], tuple[float, float]] = {}
    queue: list[tuple[float, int, tuple[float, float]]] = [(0.0, 0, start_node)]
    visited: set[tuple[float, float]] = set()
    tie = 1
    while queue:
        distance, _, node = heapq.heappop(queue)
        if node in visited:
            continue
        visited.add(node)
        if node == end_node:
            path = [node]
            while path[-1] != start_node:
                path.append(previous[path[-1]])
            path.reverse()
            return path
        for neighbor, weight in graph[node]:
            if neighbor in visited:
                continue
            candidate = distance + weight
            if candidate < distances.get(neighbor, math.inf):
                distances[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, tie, neighbor))
                tie += 1
    return None


def _snap_to_path(
    path: list[tuple[float, float]], start: Coordinate, end: Coordinate
) -> list[Coordinate]:
    return [
        start,
        *(Coordinate(longitude=lon, latitude=lat) for lon, lat in path),
        end,
    ]


def _straight_line(start: Coordinate, end: Coordinate) -> list[Coordinate]:
    return [
        Coordinate(longitude=lon, latitude=lat)
        for lon, lat in _sphere_points(start, end, 8)
    ]


def _sphere_points(
    start: Coordinate, end: Coordinate, n_points: int
) -> list[tuple[float, float]]:
    if n_points < _MIN_POLYLINE_NODES:
        return [(start.longitude, start.latitude)]
    lon1, lat1, lon2, lat2 = (
        math.radians(start.longitude),
        math.radians(start.latitude),
        math.radians(end.longitude),
        math.radians(end.latitude),
    )
    central = 2.0 * math.asin(
        math.sqrt(
            math.sin((lat2 - lat1) / 2.0) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2.0) ** 2
        )
    )
    if central < _ANTIPODE_EPSILON or math.sin(central) < _ANTIPODE_EPSILON:
        return [
            (
                start.longitude
                + (end.longitude - start.longitude) * i / (n_points - 1),
                start.latitude + (end.latitude - start.latitude) * i / (n_points - 1),
            )
            for i in range(n_points)
        ]
    points: list[tuple[float, float]] = []
    for i in range(n_points):
        t = i / (n_points - 1)
        a = math.sin((1.0 - t) * central) / math.sin(central)
        b = math.sin(t * central) / math.sin(central)
        x = a * math.cos(lat1) * math.cos(lon1) + b * math.cos(lat2) * math.cos(lon2)
        y = a * math.cos(lat1) * math.sin(lon1) + b * math.cos(lat2) * math.sin(lon2)
        z = a * math.sin(lat1) + b * math.sin(lat2)
        lat = math.degrees(math.atan2(z, math.hypot(x, y)))
        lon = math.degrees(math.atan2(y, x))
        points.append((lon, lat))
    return points


def _haversine_m(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = (
        math.radians(p1[0]),
        math.radians(p1[1]),
        math.radians(p2[0]),
        math.radians(p2[1]),
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    earth_radius_m = 6_371_000.0
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * earth_radius_m * math.asin(math.sqrt(h))


def _round7(value: float) -> float:
    return round(value, 7)
