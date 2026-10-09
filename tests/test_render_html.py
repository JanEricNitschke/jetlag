"""Tests: web rendering helpers."""

from __future__ import annotations

import html
from pathlib import Path

from jetlag_maps.format import load_seasons
from jetlag_maps.render_html import index_content


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
