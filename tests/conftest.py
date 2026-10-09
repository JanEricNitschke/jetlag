"""Shared test fixtures for the jetlag-maps test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def committed_data() -> Path:
    """Directory of committed season JSON files."""
    return Path(__file__).parent.parent / "data"


@pytest.fixture
def committed_kml() -> Path:
    """Directory of committed rendered KML files."""
    return Path(__file__).parent.parent / "kml"


@pytest.fixture
def committed_export() -> Path:
    """Directory of committed Excel/CSV export tables."""
    return Path(__file__).parent.parent / "export"
