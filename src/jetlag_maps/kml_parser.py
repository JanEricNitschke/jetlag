"""Parsing of the Google Earth KML export into per-season placemark data.

The source KML contains one flat ``Folder`` per season (named
``"Season <n> - <Season Name>"``) whose ``Placemark`` elements carry POI
names, ``Point`` coordinates and a CDATA/escaped HTML ``description``. Team
membership is only encoded in the description, in a ``Members who visited:``
block with lines such as ``1) Sam<br>2) Brian<br>``. Descriptions may also
contain ``Challenge: ...`` lines and season cross-references; members listed
there may include players from other seasons, so consumers must filter them
against the season's player roster.

Descriptions may additionally carry structured visit data: ``Episode <n>
<start> - <end>`` timing lines (one per visit of a repeatedly visited spot),
``Arrival: <HH:MM>`` wall-clock lines and tab-separated BTS flight rows. These
are parsed into dedicated fields while ``description`` stays plain text.

Descriptions may also be split into team sections introduced by a
``First and First:`` header (``and``, ``&`` or ``/`` separators). Each
section carries its own timing lines, flight rows, unknown-timing lines and
an optional ``datetime:`` value on the following line. A placemark-level
``datetime:`` value is exposed through ``datetime_text`` for section-less
placemarks.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

KML_NS = "http://www.opengis.net/kml/2.2"

_FOLDER_NAME_RE = re.compile(r"^Season (\d+) - (.+)$")
_MEMBER_LINE_RE = re.compile(r"\d\)\s*([A-Za-z]+)")
_MEMBER_BLOCK_RE = re.compile(r"Members who visited:\s*(.*)", re.DOTALL)
_EOD_DAY_RE = re.compile(r"Day\s*#?\s*(\d+)")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINE_RE = re.compile(r"\n{2,}")
_EPISODE_LINE_RE = re.compile(
    r"^Episode\s*(\d+)\s*:?\s*"
    r"(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*"
    r"(\d{1,2}:\d{2}(?::\d{2})?)$",
    re.IGNORECASE,
)
_EPISODE_UNKNOWN_RE = re.compile(
    r"^Episode\s*(\d+)\s*:?\s*\?\?:\?\?\s*-\s*\?\?:\?\?$",
    re.IGNORECASE,
)
_ARRIVAL_RE = re.compile(r"^Arrival:\s*(\d{1,2}:\d{2})$", re.IGNORECASE)
_FLIGHT_ROW_RE = re.compile(
    r"^([A-Z0-9]{2})\t(\d{2}/\d{2}/\d{4})\t(\d+)\t([A-Z0-9]+)\t"
    r"([A-Z]{3})\t(\d{1,2}:\d{2})\t(\d{1,2}:\d{2})$"
)
_DATETIME_LINE_RE = re.compile(r"^datetime\s*:\s*$", re.IGNORECASE)
_SECTION_HEADER_RE = re.compile(
    r"^([A-Za-z]+(?:\s*(?:and|&|/)\s*[A-Za-z]+)+):$", re.IGNORECASE
)
_SECTION_SPLIT_RE = re.compile(r"\s*(?:and|&|/)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class _VideoSegment:
    """One timed visit of a placemark in an episode.

    Attributes
    ----------
    episode
        Episode number from an ``Episode <n> ...`` line.
    start
        Raw segment start, either ``MM:SS`` or ``HH:MM:SS``.
    end
        Raw segment end, either ``MM:SS`` or ``HH:MM:SS``.
    """

    episode: int
    start: str
    end: str


@dataclass(frozen=True)
class _FlightRow:
    """One tab-separated BTS flight row of a placemark description.

    Attributes
    ----------
    carrier
        Two-character carrier code.
    date
        Flight date in ``MM/DD/YYYY`` form.
    flight_number
        Flight number, zero-padded in the source.
    tail
        Aircraft tail number.
    airport
        IATA code of the other airport of the leg.
    scheduled
        Scheduled departure or arrival time as ``HH:MM``.
    actual
        Actual departure or arrival time as ``HH:MM``.
    """

    carrier: str
    date: str
    flight_number: str
    tail: str
    airport: str
    scheduled: str
    actual: str


@dataclass(frozen=True)
class _TeamSection:
    """One ``First and First:`` section of a placemark description.

    Attributes
    ----------
    members
        First names parsed from the section header, in header order.
    video_segments
        Episode timings of the section, in line order.
    flight_rows
        Structured BTS flight rows of the section.
    datetime
        Raw value of the section's ``datetime:`` line, ``None`` if absent.
    unknown_video_lines
        Raw section-scoped ``Episode <n> ??:?? - ??:??`` lines.
    """

    members: list[str]
    video_segments: list[_VideoSegment]
    flight_rows: list[_FlightRow]
    datetime: str | None
    unknown_video_lines: list[str]


@dataclass(frozen=True)
class KmlPlacemark:
    """A single point placemark of a season folder.

    Attributes
    ----------
    name
        Placemark name (POI name, not necessarily unique).
    longitude
        Point longitude from the ``<coordinates>`` element.
    latitude
        Point latitude from the ``<coordinates>`` element.
    member_names
        Raw first names parsed from the ``Members who visited:`` block.
        May contain players from other seasons and is not normalized.
    description
        The full description as plain text, with tags stripped.
    style_url
        The ``styleUrl`` of the placemark, if any.
    end_of_day
        Day number parsed from ``"... Ending Spot Day N"`` names,
        ``None`` for regular stops and ending spots without a day number.
    video_segments
        Episode timings of repeated visits, in line order. Unknown-timing
        lines are not included.
    unknown_video_lines
        Raw ``Episode <n> ??:?? - ??:??`` lines with unknown timings.
    arrivals
        Raw ``HH:MM`` wall-clock times from ``Arrival:`` lines.
    flight_rows
        Structured BTS flight rows found in tab-separated description lines.
    datetime_text
        Raw value of the placemark-level ``datetime:`` line, ``None`` when
        absent or when the description is split into team sections.
    sections
        Team sections parsed from ``First and First:`` headers, empty when
        the description has none.
    """

    name: str
    longitude: float
    latitude: float
    member_names: list[str]
    description: str
    style_url: str | None
    end_of_day: int | None
    video_segments: list[_VideoSegment]
    unknown_video_lines: list[str]
    arrivals: list[str]
    flight_rows: list[_FlightRow]
    datetime_text: str | None
    sections: list[_TeamSection]


@dataclass(frozen=True)
class KmlSeason:
    """All placemarks of one season folder.

    Attributes
    ----------
    number
        Season number parsed from the folder name.
    name
        Season name parsed from the folder name.
    placemarks
        Placemarks in document order.
    """

    number: int
    name: str
    placemarks: list[KmlPlacemark]


def _parse_description(description: str) -> list[str]:
    """Extract member first names from a description.

    Parameters
    ----------
    description
        Raw description text of a placemark.

    Returns
    -------
    list[str]
        Member first names in listing order.
    """
    text = html.unescape(description).replace("\xa0", " ")
    block = _MEMBER_BLOCK_RE.search(text)
    if block is None:
        return []
    return _MEMBER_LINE_RE.findall(block.group(1))


def _description_text(html_text: str) -> str:
    """Convert a placemark's HTML description to plain text.

    Parameters
    ----------
    html_text
        Raw HTML description of a placemark.

    Returns
    -------
    str
        The description with tags stripped, entities unescaped and
        blank lines collapsed.
    """
    text = html.unescape(html_text).replace("\xa0", " ")
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    return _BLANK_LINE_RE.sub("\n", text).strip()


def _scan_lines(
    raw_lines: list[str],
) -> tuple[list[_VideoSegment], list[str], list[str], list[_FlightRow], str | None]:
    """Extract structured visit data from plain-text lines.

    Parameters
    ----------
    raw_lines
        Description lines as produced by :func:`_description_text`.

    Returns
    -------
    tuple[list[_VideoSegment], list[str], list[str], list[_FlightRow], str | None]
        Video segments, raw unknown-timing lines, raw arrival times, flight
        rows and the first ``datetime:`` value (taken from the following
        line), each in line order. Lines that match none of the patterns are
        ignored.
    """
    segments: list[_VideoSegment] = []
    unknown: list[str] = []
    arrivals: list[str] = []
    flights: list[_FlightRow] = []
    datetime_text: str | None = None
    lines = [line.strip() for line in raw_lines]
    index = 0
    while index < len(lines):
        line = lines[index]
        if _DATETIME_LINE_RE.match(line):
            if datetime_text is None and index + 1 < len(lines):
                datetime_text = lines[index + 1]
            index += 2
            continue
        episode = _EPISODE_LINE_RE.match(line)
        if episode is not None:
            segments.append(
                _VideoSegment(
                    episode=int(episode.group(1)),
                    start=episode.group(2),
                    end=episode.group(3),
                )
            )
            index += 1
            continue
        if _EPISODE_UNKNOWN_RE.match(line):
            unknown.append(line)
            index += 1
            continue
        arrival = _ARRIVAL_RE.match(line)
        if arrival is not None:
            arrivals.append(arrival.group(1))
            index += 1
            continue
        flight = _FLIGHT_ROW_RE.match(line)
        if flight is not None:
            flights.append(
                _FlightRow(
                    carrier=flight.group(1),
                    date=flight.group(2),
                    flight_number=flight.group(3),
                    tail=flight.group(4),
                    airport=flight.group(5),
                    scheduled=flight.group(6),
                    actual=flight.group(7),
                )
            )
        index += 1
    return segments, unknown, arrivals, flights, datetime_text


def _section_from_lines(members: list[str], lines: list[str]) -> _TeamSection:
    """Build a :class:`_TeamSection` from its header members and body lines."""
    segments, unknown, _arrivals, flights, datetime_text = _scan_lines(lines)
    return _TeamSection(
        members=members,
        video_segments=segments,
        flight_rows=flights,
        datetime=datetime_text,
        unknown_video_lines=unknown,
    )


def _parse_sections(text: str) -> list[_TeamSection]:
    """Split a plain-text description into ``First and First:`` sections.

    Parameters
    ----------
    text
        Plain-text description as produced by :func:`_description_text`.

    Returns
    -------
    list[_TeamSection]
        Sections in document order. Lines before the first section header
        are ignored; empty list when the description has no section headers.
    """
    sections: list[_TeamSection] = []
    members: list[str] | None = None
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        header = _SECTION_HEADER_RE.match(line)
        if header is not None:
            if members is not None:
                sections.append(_section_from_lines(members, lines))
            members = _SECTION_SPLIT_RE.split(header.group(1))
            lines = []
            continue
        if members is not None:
            lines.append(line)
    if members is not None:
        sections.append(_section_from_lines(members, lines))
    return sections


def _parse_placemark(placemark: ET.Element) -> KmlPlacemark:
    """Convert a KML placemark element into a :class:`KmlPlacemark`.

    Parameters
    ----------
    placemark
        The ``<Placemark>`` element to convert.

    Returns
    -------
    KmlPlacemark
        The parsed placemark. Missing descriptions yield no members;
        ``Point`` coordinates are used and ``LookAt`` is ignored.
    """
    ns = f"{{{KML_NS}}}"
    name_element = placemark.find(f"{ns}name")
    name = name_element.text if name_element is not None and name_element.text else ""
    description_element = placemark.find(f"{ns}description")
    description = (
        description_element.text
        if description_element is not None
        and description_element.text
        and description_element.text.strip()
        else ""
    )
    member_names = _parse_description(description) if description else []
    text = _description_text(description) if description else ""
    segments: list[_VideoSegment] = []
    unknown: list[str] = []
    arrivals: list[str] = []
    flights: list[_FlightRow] = []
    datetime_text: str | None = None
    sections: list[_TeamSection] = []
    if text:
        segments, unknown, arrivals, flights, datetime_text = _scan_lines(
            text.splitlines()
        )
        sections = _parse_sections(text)
        if sections:
            datetime_text = None
    coordinates_element = placemark.find(f"{ns}Point/{ns}coordinates")
    longitude, latitude = 0.0, 0.0
    if coordinates_element is not None and coordinates_element.text:
        parts = coordinates_element.text.strip().split(",")
        longitude = float(parts[0])
        latitude = float(parts[1])
    style_element = placemark.find(f"{ns}styleUrl")
    style_url = style_element.text if style_element is not None else None
    end_of_day = None
    if "Ending Spot" in name:
        day = _EOD_DAY_RE.search(name)
        if day is not None:
            end_of_day = int(day.group(1))
    return KmlPlacemark(
        name=name,
        longitude=longitude,
        latitude=latitude,
        member_names=member_names,
        description=text,
        style_url=style_url,
        end_of_day=end_of_day,
        video_segments=segments,
        unknown_video_lines=unknown,
        arrivals=arrivals,
        flight_rows=flights,
        datetime_text=datetime_text,
        sections=sections,
    )


def parse_kml(path: Path) -> list[KmlSeason]:
    """Parse a Google Earth KML export into per-season placemark data.

    Parameters
    ----------
    path
        Path of the KML file to parse.

    Returns
    -------
    list[KmlSeason]
        One entry per season folder (``Season N - Name``), in document
        order. Folders whose name does not match the expected pattern are
        skipped.
    """
    root = ET.parse(path).getroot()  # noqa: S314
    ns = f"{{{KML_NS}}}"
    document = root.find(f"{ns}Document")
    if document is None:
        return []
    seasons: list[KmlSeason] = []
    for folder in document.findall(f"{ns}Folder"):
        name_element = folder.find(f"{ns}name")
        folder_name = (
            name_element.text if name_element is not None and name_element.text else ""
        )
        match = _FOLDER_NAME_RE.match(folder_name)
        if match is None:
            continue
        placemarks = [
            _parse_placemark(placemark)
            for placemark in folder.findall(f"{ns}Placemark")
        ]
        seasons.append(
            KmlSeason(
                number=int(match.group(1)),
                name=match.group(2),
                placemarks=placemarks,
            )
        )
    return seasons
