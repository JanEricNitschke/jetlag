"""Tests: web rendering helpers."""

from __future__ import annotations

import html
from pathlib import Path

from jetlag_maps.format import load_seasons
from jetlag_maps.render_html import _unwrapped, index_content


def test_unwrapped_keeps_dateline_legs_continuous() -> None:
    """Legs crossing the antimeridian continue eastward, not across the map."""
    points = _unwrapped(
        [(-33.9, 151.2), (-25.0, 179.9), (-17.8, -179.9), (0.0, -160.0)]
    )
    expected = [(-33.9, 151.2), (-25.0, 179.9), (-17.8, 180.1), (0.0, 200.0)]
    assert points == expected


def test_unwrapped_untouched_without_dateline() -> None:
    """Legs staying on one side of the antimeridian are passed through."""
    points = [(39.9, -105.0), (40.6, -73.8), (45.6, 8.7)]
    assert _unwrapped(points) == points


def test_index_links_every_season(committed_data: Path) -> None:
    """The landing page links the combined map and every season map."""
    seasons = load_seasons(committed_data)
    content = index_content(seasons)
    assert 'href="all.html"' in content
    for season in seasons:
        assert f'href="{season.slug}.html"' in content
        label = html.escape(f"Season {season.season}: {season.name}")
        assert label in content


def test_index_omits_combined_link_for_single_season(committed_data: Path) -> None:
    """A single season gets no link to a combined map."""
    season = load_seasons(committed_data)[0]
    content = index_content([season])
    assert 'href="all.html"' not in content
    assert f'href="{season.slug}.html"' in content
