"""Parsing of the Google Earth KML export into per-season placemark data.

The source KML contains one flat ``Folder`` per season (named
``"Season <n> - <Season Name>"``) whose ``Placemark`` elements carry POI
names, ``Point`` coordinates and a CDATA/escaped HTML ``description``. Team
membership is only encoded in the description, in a ``Members who visited:``
block with lines such as ``1) Sam<br>2) Brian<br>``. Descriptions may also
contain ``Challenge: ...`` lines and season cross-references; members listed
there may include players from other seasons, so consumers must filter them
against the season's player roster.
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
    """

    name: str
    longitude: float
    latitude: float
    member_names: list[str]
    description: str
    style_url: str | None
    end_of_day: int | None


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
        description=_description_text(description) if description else "",
        style_url=style_url,
        end_of_day=end_of_day,
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
