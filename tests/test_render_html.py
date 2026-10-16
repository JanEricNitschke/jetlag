"""Tests: web rendering helpers."""

from __future__ import annotations

import html
from pathlib import Path

from jetlag_maps.format import load_seasons
from jetlag_maps.render_html import (
    _owned_leg_chunks,
    _segment_key,
    _unwrapped,
    index_content,
)


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


def test_segment_key_is_direction_insensitive() -> None:
    """Two legs riding the same track in opposite directions share keys."""
    a = (35.6812362, 139.7671248)
    b = (35.6290, 139.7400)
    assert _segment_key(a, b) == _segment_key(b, a)


def test_segment_key_degenerate_segments_are_skipped() -> None:
    """Zero-length segments never take part in deduplication."""
    assert _segment_key((35.5, 139.7), (35.5, 139.7)) is None


def test_identical_legs_draw_once_with_merged_popup_ids() -> None:
    """A repeated leg borrows everything; the drawn run lists both."""
    points = [(35.0, 139.0), (35.1, 139.1), (35.2, 139.2)]
    runs = _owned_leg_chunks([points, points])
    assert len(runs[0]) == 1
    chunk_pts, popup_ids, owned = runs[0][0]
    assert chunk_pts == points
    assert popup_ids == [0, 1]
    assert owned is True
    # the repeated leg contributes one borrowed run: no popup, arrows only
    assert runs[1] == [(points, [], False)]


def test_partial_overlap_keeps_only_the_new_remainder() -> None:
    """A later leg keeps only the segments nobody drew yet."""
    leg_a = [(35.0, 139.0), (35.1, 139.1), (35.2, 139.2)]
    leg_b = [(35.1, 139.1), (35.2, 139.2), (35.3, 139.3)]
    runs = _owned_leg_chunks([leg_a, leg_b])
    assert len(runs[0]) == 1
    assert runs[0][0][0] == leg_a
    assert runs[0][0][1] == [0, 1]  # leg b rides leg a's corridor
    assert runs[0][0][2] is True
    # leg b: borrowed run over the shared corridor, owned run for the tail
    assert runs[1] == [
        ([(35.1, 139.1), (35.2, 139.2)], [], False),
        ([(35.2, 139.2), (35.3, 139.3)], [1], True),
    ]


def test_reverse_leg_borrows_the_forward_segments() -> None:
    """Out-and-back travel on the same track draws the track once."""
    forward = [(35.0, 139.0), (35.1, 139.1)]
    backward = [(35.1, 139.1), (35.0, 139.0)]
    runs = _owned_leg_chunks([forward, backward])
    assert runs[0][0][1] == [0, 1]
    assert runs[1] == [(backward, [], False)]


def test_alternating_owned_and_borrowed_runs() -> None:
    """A leg mixing novel and already-drawn segments yields both runs."""
    leg_a = [(35.0, 139.0), (35.1, 139.1)]
    leg_b = [(35.9, 139.9), (35.0, 139.0), (35.1, 139.1), (35.2, 139.2)]
    runs = _owned_leg_chunks([leg_a, leg_b])
    # leg b: novel lead-in, borrowed middle, novel tail
    assert [owned for _pts, _ids, owned in runs[1]] == [True, False, True]


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
