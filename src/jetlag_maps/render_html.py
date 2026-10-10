"""Render season JSON files as interactive folium maps."""

from __future__ import annotations

import html
import json
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import folium
from folium.plugins import MarkerCluster, PolyLineTextPath

from jetlag_maps.format import (
    SeasonFile,
    format_video_time,
    join_players,
    load_seasons,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from jetlag_maps.cli import WebArgs
    from jetlag_maps.format import Journey, Stop, ToNext

_POPUP_MAX_WIDTH = 320
_DOT_PX = 22
_DOT_EDGE_PX = 2
_EOD_EDGE_PX = 5
_MARKER_EDGE_COLOR = "#FFFFFF"
_CLUSTER_OPTIONS = {"maxClusterRadius": 20, "spiderfyDistanceMultiplier": 1.5}
_CLUSTER_ICON_SCALE_CSS = ".marker-cluster{transform:scale(1.4)}"
_EOD_COLOR = "#FFEE58"
_EOD_BADGE_CSS = (
    "background-color:#FFEE58;padding:2px 6px;border-radius:4px;font-weight:bold"
)


class MarkerMode(StrEnum):
    """How coincident markers of different teams are shown."""

    CLUSTER = "cluster"
    SPLIT = "split"


def render(args: WebArgs) -> None:
    """Render season JSON file(s) under ``args.data`` as HTML in ``args.out``."""
    seasons = load_seasons(args.data)
    args.out.mkdir(parents=True, exist_ok=True)
    for season in seasons:
        _build_map(
            [(f"Season {season.season}: {season.name}", season)], args.markers
        ).save(str(args.out / f"{season.slug}.html"))
    if len(seasons) > 1:
        _build_map(
            [(f"Season {season.season}: {season.name}", season) for season in seasons],
            args.markers,
        ).save(str(args.out / "all.html"))
    (args.out / "index.html").write_text(index_content(seasons), encoding="utf-8")


def index_content(seasons: Sequence[SeasonFile]) -> str:
    """Render the landing page linking the combined and per-season maps."""
    items: list[str] = []
    if len(seasons) > 1:
        items.append('<li><a href="all.html">All seasons</a></li>')
    for season in seasons:
        label = html.escape(f"Season {season.season}: {season.name}")
        items.append(f'<li><a href="{season.slug}.html">{label}</a></li>')
    links = "\n".join(items)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Jet Lag Maps</title>\n"
        "</head>\n"
        "<body>\n"
        "<h1>Jet Lag: The Game - Maps</h1>\n"
        '<ul style="font-family:sans-serif;font-size:16px;line-height:1.8">\n'
        f"{links}\n"
        "</ul>\n"
        "</body>\n"
        "</html>\n"
    )


def _stop_players(stop: Stop, journey: Journey) -> Sequence[str]:
    """Return the players present at a stop, in display order."""
    return [*(stop.players_override or journey.team), *stop.additional_players]


def _stop_popup(stop: Stop, journey: Journey) -> str:
    """Build the HTML popup of a stop marker."""
    parts = [f"<div><b>{html.escape(stop.name)}</b></div>"]
    names = ", ".join(html.escape(str(p)) for p in _stop_players(stop, journey))
    parts.append(f"<div>Players: {names}</div>")
    if stop.when is not None:
        stamp = stop.when.start.strftime("%Y-%m-%d %H:%M")
        if stop.when.end is not None:
            stamp += f" - {stop.when.end.strftime('%Y-%m-%d %H:%M')}"
        parts.append(f"<div>Time: {stamp}</div>")
    if stop.video is not None:
        stamp = (
            f"{format_video_time(stop.video.start)}"
            f" - {format_video_time(stop.video.end)}"
        )
        parts.append(f"<div>Video: Episode {stop.video.episode}, {stamp}</div>")
    for note in stop.notes:
        parts.append(f"<div>Note: {html.escape(note)}</div>")
    for day in stop.end_of_day:
        parts.append(f'<span style="{_EOD_BADGE_CSS}">End of Day {day}</span>')
    return "<br>".join(parts)


def _leg_popup(leg: ToNext) -> str:
    """Build the HTML popup of a leg polyline."""
    parts = [f"<div><b>{html.escape(leg.mode.value)}</b></div>"]
    if leg.note:
        parts.append(f"<div>{html.escape(leg.note)}</div>")
    if leg.players_override is not None:
        names = ", ".join(html.escape(str(p)) for p in leg.players_override)
        parts.append(f"<div>Players: {names}</div>")
    return "<br>".join(parts)


def _add_clustered_stop_markers(cluster: MarkerCluster, journey: Journey) -> None:
    """Add one colored dot marker per stop of the journey to a cluster."""
    for stop in journey.stops:
        _stop_marker(
            (stop.coordinate.latitude, stop.coordinate.longitude), journey, stop
        ).add_to(cluster)


def _add_split_stop_markers(group: folium.FeatureGroup, season: SeasonFile) -> None:
    """Add one composite marker per location, colored per team present.

    The dot is split into one conic segment per journey that has a stop
    at the location, so coincident stops stay distinguishable at any
    zoom level without any geographic offset.
    """
    by_location: dict[tuple[float, float], list[tuple[Journey, Stop]]] = {}
    for journey in season.journeys:
        for stop in journey.stops:
            key = (stop.coordinate.latitude, stop.coordinate.longitude)
            by_location.setdefault(key, []).append((journey, stop))
    for (latitude, longitude), entries in by_location.items():
        colors = [str(journey.color) for journey, _ in entries]
        has_eod = any(stop.end_of_day for _, stop in entries)
        tooltip = " / ".join(dict.fromkeys(_stop_tooltip(stop) for _, stop in entries))
        popup = folium.Popup(
            "<hr>".join(_stop_popup(stop, journey) for journey, stop in entries),
            max_width=_POPUP_MAX_WIDTH,
        )
        folium.Marker(
            location=(latitude, longitude),
            icon=_dot_icon(colors, eod=has_eod),
            tooltip=tooltip,
            popup=popup,
        ).add_to(group)


def _stop_marker(
    location: tuple[float, float], journey: Journey, stop: Stop
) -> folium.Marker:
    """Build a single-journey stop marker with a solid dot icon."""
    return folium.Marker(
        location=location,
        icon=_dot_icon([str(journey.color)], eod=bool(stop.end_of_day)),
        tooltip=_stop_tooltip(stop),
        popup=folium.Popup(_stop_popup(stop, journey), max_width=_POPUP_MAX_WIDTH),
    )


def _dot_icon(colors: Sequence[str], *, eod: bool) -> folium.DivIcon:
    """Build a round dot icon, split into one conic segment per color.

    The gold end-of-day ring is drawn as a box-shadow spread, which is
    concentric with the dot by construction and wraps around the normal
    marker without changing it.
    """
    segments = ", ".join(
        f"{color} {index * 100 // len(colors)}% {(index + 1) * 100 // len(colors)}%"
        for index, color in enumerate(colors)
    )
    marker_size = _DOT_PX + 2 * _DOT_EDGE_PX
    size = marker_size
    shadows = "0 0 2px #333"
    if eod:
        size += 2 * _EOD_EDGE_PX
        shadows = f"0 0 0 {_EOD_EDGE_PX}px {_EOD_COLOR}, {shadows}"
    return folium.DivIcon(
        html=(
            f'<div style="box-sizing:border-box;'
            f"width:{marker_size}px;height:{marker_size}px;border-radius:50%;"
            f"background:conic-gradient({segments});"
            f"border:{_DOT_EDGE_PX}px solid {_MARKER_EDGE_COLOR};"
            f'box-shadow:{shadows}"></div>'
        ),
        icon_size=(size, size),
        icon_anchor=(size // 2, size // 2),
    )


def _stop_tooltip(stop: Stop) -> str:
    tooltip = html.escape(stop.name)
    if stop.end_of_day:
        days = ", ".join(str(day) for day in stop.end_of_day)
        tooltip = f"{tooltip} (End of Day {days})"
    return tooltip


_ANTIMERIDIAN_DEG = 180.0
_FULL_TURN_DEG = 360.0


def _unwrapped(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Shift longitudes so legs crossing the antimeridian stay continuous.

    Each longitude is replaced by its equivalent (``± 360``) closest to the
    previous point, so a dateline-crossing leg continues into the next
    world copy instead of drawing a line across the whole map. Web-mercator
    maps like Leaflet render longitudes beyond ±180 as the wrapped world
    copy, keeping the line continuous.
    """
    result = [points[0]]
    for point in points[1:]:
        latitude, longitude = point
        previous = result[-1][1]
        while longitude - previous > _ANTIMERIDIAN_DEG:
            longitude -= _FULL_TURN_DEG
        while longitude - previous < -_ANTIMERIDIAN_DEG:
            longitude += _FULL_TURN_DEG
        result.append((latitude, longitude))
    return result


_LINE_DASHES = ("", "14 8", "2 7")
"""Dash patterns per journey index, so legs of different journeys that run
along the same corridor stay distinguishable instead of hiding each other."""


def _add_leg_lines(
    group: folium.FeatureGroup, journey: Journey, journey_index: int = 0
) -> None:
    """Add one journey-colored, arrowed polyline per ``to_next`` leg."""
    color = str(journey.color)
    dash = _LINE_DASHES[journey_index % len(_LINE_DASHES)]
    for stop, nxt in zip(journey.stops, journey.stops[1:], strict=False):
        for leg in stop.to_next:
            coordinates = leg.geometry or [stop.coordinate, nxt.coordinate]
            points = _unwrapped([(c.latitude, c.longitude) for c in coordinates])
            line = folium.PolyLine(
                points,
                color=color,
                weight=4,
                opacity=0.8,
                popup=_leg_popup(leg),
                dash_array=dash,
            )
            line.add_to(group)
            _add_direction_arrows(group, line, color)


def _add_direction_arrows(
    group: folium.FeatureGroup, line: folium.PolyLine, color: str
) -> None:
    """Overlay travel-direction arrows along a leg polyline."""
    PolyLineTextPath(
        line,
        text="          →          ",
        repeat=True,
        center=True,
        offset=8,
        attributes={
            "fill": color,
            "stroke": "#000000",
            "stroke-width": "2",
            "paint-order": "stroke",
            "font-weight": "900",
            "font-size": "24px",
        },
    ).add_to(group)


def _journey_points(journey: Journey) -> list[tuple[float, float]]:
    """Collect all ``(latitude, longitude)`` points of a journey."""
    points = [(s.coordinate.latitude, s.coordinate.longitude) for s in journey.stops]
    for stop in journey.stops:
        for leg in stop.to_next:
            points.extend((c.latitude, c.longitude) for c in leg.geometry)
    return points


def _add_legend(map_: folium.Map, layers: Sequence[tuple[str, SeasonFile]]) -> None:
    """Add a fixed legend mapping journey colors to teams."""
    rows: list[str] = []
    for name, season in layers:
        rows.append(
            f'<div style="font-weight:bold;margin-top:6px">{html.escape(name)}</div>'
        )
        for journey in season.journeys:
            rows.append(
                "<div>"
                '<span style="display:inline-block;width:12px;height:12px;'
                f"border-radius:50%;background:{journey.color};"
                'margin-right:6px;vertical-align:middle"></span>'
                f"{html.escape(join_players(journey.team))}"
                "</div>"
            )
    has_eod = any(
        stop.end_of_day
        for _, season in layers
        for journey in season.journeys
        for stop in journey.stops
    )
    if has_eod:
        rows.append(
            "<div>"
            '<span style="display:inline-block;width:12px;height:12px;'
            f"border:{_EOD_EDGE_PX}px solid {_EOD_COLOR};border-radius:50%;"
            'margin-right:6px;vertical-align:middle"></span>'
            "= stop ends a day (End of Day N)"
            "</div>"
        )
    legend = (
        '<div style="position:fixed;bottom:20px;left:20px;z-index:9999;'
        "background:white;padding:10px 14px;border:1px solid #999;"
        "border-radius:4px;font-family:sans-serif;font-size:13px;"
        f'line-height:1.6">{"".join(rows)}</div>'
    )
    map_.get_root().html.add_child(folium.Element(legend))  # pyrefly: ignore[missing-attribute]


def _scale_cluster_icons(map_: folium.Map) -> None:
    """Enlarge the cluster bubbles so mixed clusters stay readable."""
    map_.get_root().html.add_child(  # pyrefly: ignore[missing-attribute]
        folium.Element(f"<style>{_CLUSTER_ICON_SCALE_CSS}</style>")
    )


def _add_boundary_overlay(map_: folium.Map) -> None:
    """Add combined admin-1 and admin-2 boundary GeoJSON overlay layers."""
    geo_dir = Path(__file__).parent.parent.parent / "geo"
    for level, label, visible in (
        ("ADM1", "States/Regions", True),
        ("ADM2", "Counties/Districts", False),
    ):
        features: list[dict[str, object]] = []
        for path in sorted(geo_dir.glob(f"geoBoundaries-*-{level}_simplified.geojson")):
            data = json.loads(path.read_text(encoding="utf-8"))
            features.extend(data.get("features", []))
        if not features:
            continue
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            name=label,
            show=visible,
            style_function=lambda _f: {  # pyrefly: ignore[implicit-any-lambda]
                "fillOpacity": 0,
                "weight": 1,
                "color": "#555555",
            },
            tooltip=folium.GeoJsonTooltip(fields=["shapeName"], aliases=[""]),
        ).add_to(map_)


def _build_map(
    layers: Sequence[tuple[str, SeasonFile]], markers: MarkerMode
) -> folium.Map:
    """Build a folium map with one toggleable overlay layer per entry."""
    map_ = folium.Map(tiles=None)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="World Topo",
    ).add_to(map_)
    folium.TileLayer(
        tiles="OpenStreetMap",
        name="OpenStreetMap",
        show=False,
    ).add_to(map_)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite",
        show=False,
    ).add_to(map_)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Light Gray",
        show=False,
    ).add_to(map_)
    folium.TileLayer(
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr="OpenTopoMap",
        name="OpenTopoMap",
        show=False,
    ).add_to(map_)
    _add_boundary_overlay(map_)
    all_points: list[tuple[float, float]] = []
    for name, season in layers:
        group = folium.FeatureGroup(name=name)
        match markers:
            case MarkerMode.CLUSTER:
                cluster = MarkerCluster(options=_CLUSTER_OPTIONS)
                for journey_index, journey in enumerate(season.journeys):
                    _add_clustered_stop_markers(cluster, journey)
                    _add_leg_lines(group, journey, journey_index)
                    all_points.extend(_journey_points(journey))
                group.add_child(cluster)
            case MarkerMode.SPLIT:
                _add_split_stop_markers(group, season)
                for journey_index, journey in enumerate(season.journeys):
                    _add_leg_lines(group, journey, journey_index)
                    all_points.extend(_journey_points(journey))
        group.add_to(map_)
    if markers == MarkerMode.CLUSTER:
        _scale_cluster_icons(map_)
    folium.LayerControl(collapsed=False).add_to(map_)
    _add_legend(map_, layers)
    if all_points:
        lats = [lat for lat, _ in all_points]
        lons = [lon for _, lon in all_points]
        map_.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    return map_
