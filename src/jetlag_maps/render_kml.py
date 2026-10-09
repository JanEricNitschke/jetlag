"""Render season JSON files as KML for Google Earth / My Maps."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING
from xml.etree.ElementTree import (
    Element,
    SubElement,
    indent,
    tostring,
)

from jetlag_maps.format import (
    SeasonFile,
    format_video_time,
    join_players,
    load_seasons,
)
from jetlag_maps.kml_parser import KML_NS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from jetlag_maps.cli import RenderArgs
    from jetlag_maps.config import Color, Player
    from jetlag_maps.format import Coordinate, Journey, Stop, ToNext, When

PIN_HREF = "https://maps.google.com/mapfiles/kml/pushpin/wht-blank.png"
LINE_WIDTH = "3"
PIN_SCALE = "1.2"
_HEX_COLOR = re.compile(r"[0-9a-fA-F]{6}")


class _InvalidColorError(ValueError):
    def __init__(self, value: object) -> None:
        super().__init__(f"invalid color {value!r}")


def render(args: RenderArgs) -> None:
    """Render season JSON file(s) under ``args.data`` into KML in ``args.out``."""
    args.out.mkdir(parents=True, exist_ok=True)
    for season in load_seasons(args.data):
        (args.out / f"{season.slug}.kml").write_bytes(kml_content(season))


def kml_content(season: SeasonFile) -> bytes:
    """Render a season into the exact bytes of its KML file."""
    root = Element(f"{{{KML_NS}}}kml")
    document = _sub(root, "Document")
    _sub(document, "name", f"Season {season.season}: {season.name}")
    for journey in season.journeys:
        _render_journey(document, journey)
    indent(root)
    return tostring(
        root, encoding="utf-8", xml_declaration=True, default_namespace=KML_NS
    )


def _render_journey(document: Element, journey: Journey) -> None:
    folder = _sub(document, "Folder")
    _sub(folder, "name", join_players(journey.team))
    for stop in journey.stops:
        _render_stop(folder, journey, stop)
    _render_legs(folder, journey)


def _render_stop(folder: Element, journey: Journey, stop: Stop) -> None:
    placemark = _sub(folder, "Placemark")
    _sub(placemark, "name", stop.name)
    _sub(placemark, "description", _stop_description(stop, journey.team))
    if stop.when is not None:
        _add_time_span(placemark, stop.when)
    _add_pin_style(placemark, journey.color)
    point = _sub(placemark, "Point")
    _sub(point, "coordinates", _point_text(stop.coordinate))


def _render_legs(folder: Element, journey: Journey) -> None:
    for stop, nxt in zip(journey.stops, journey.stops[1:], strict=False):
        for leg in stop.to_next:
            coordinates = leg.geometry or [stop.coordinate, nxt.coordinate]
            placemark = _sub(folder, "Placemark")
            _sub(placemark, "name", f"{stop.name} to {nxt.name} ({leg.mode.value})")
            _sub(placemark, "description", _leg_description(leg))
            _add_line_style(placemark, journey.color)
            line_string = _sub(placemark, "LineString")
            _sub(line_string, "coordinates", _line_text(coordinates))


def _add_time_span(placemark: Element, when: When) -> None:
    time_span = _sub(placemark, "TimeSpan")
    _sub(time_span, "begin", when.start.isoformat())
    if when.end is not None:
        _sub(time_span, "end", when.end.isoformat())


def _add_pin_style(placemark: Element, color: Color) -> None:
    style = _sub(placemark, "Style")
    icon_style = _sub(style, "IconStyle")
    _sub(icon_style, "color", _kml_color(color))
    _sub(icon_style, "scale", PIN_SCALE)
    icon = _sub(icon_style, "Icon")
    _sub(icon, "href", PIN_HREF)


def _add_line_style(placemark: Element, color: Color) -> None:
    style = _sub(placemark, "Style")
    line_style = _sub(style, "LineStyle")
    _sub(line_style, "color", _kml_color(color))
    _sub(line_style, "width", LINE_WIDTH)


def _stop_description(stop: Stop, team: Sequence[Player]) -> str:
    players = stop.players_override if stop.players_override is not None else team
    label = "Players" if stop.players_override is not None else "Team"
    lines = [f"<b>{label}:</b> {join_players(players)}"]
    if stop.additional_players:
        lines.append(f"<b>Also present:</b> {join_players(stop.additional_players)}")
    if stop.video is not None:
        start = format_video_time(stop.video.start)
        end = format_video_time(stop.video.end)
        lines.append(f"<b>Video:</b> Episode {stop.video.episode}, {start}-{end}")
    if stop.notes:
        items = "".join(f"<li>{html.escape(note)}</li>" for note in stop.notes)
        lines.append(f"<b>Notes:</b><ul>{items}</ul>")
    if stop.end_of_day:
        days = ", ".join(str(day) for day in stop.end_of_day)
        lines.append(f"<b>End of day {days}</b>")
    return "<br>".join(lines)


def _leg_description(leg: ToNext) -> str:
    lines = [f"<b>Mode:</b> {leg.mode.value}"]
    if leg.note:
        lines.append(html.escape(leg.note))
    if leg.players_override is not None:
        lines.append(f"<b>Players:</b> {join_players(leg.players_override)}")
    return "<br>".join(lines)


def _kml_color(color: Color) -> str:
    digits = color.removeprefix("#").lower()
    if not _HEX_COLOR.fullmatch(digits):
        raise _InvalidColorError(color)
    return f"ff{digits[4:]}{digits[2:4]}{digits[:2]}"


def _point_text(coordinate: Coordinate) -> str:
    return f"{coordinate.longitude},{coordinate.latitude}"


def _line_text(coordinates: Sequence[Coordinate]) -> str:
    return " ".join(_point_text(coordinate) for coordinate in coordinates)


def _sub(parent: Element, tag: str, text: str | None = None) -> Element:
    element = SubElement(parent, f"{{{KML_NS}}}{tag}")
    if text is not None:
        element.text = text
    return element
