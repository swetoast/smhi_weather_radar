"""Tests for radar coverage of the location."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.smhi_radar.coverage import coverage, parse_status
from custom_components.smhi_radar.exceptions import SmhiRadarDataError

from .conftest import NEWEST, radar_status

BORAS, JONKOPING, STOCKHOLM = (57.721, 12.940), (57.78, 14.16), (59.33, 18.07)


def test_vara_outage_leaves_boras_uncovered() -> None:
    """Without Vara, Boras is out of reach; Jonkoping is filled in by Atvidaberg."""
    status = parse_status(radar_status({"vara": "2026-10-22T08:09:56Z"}))
    boras = coverage(status, *BORAS, NEWEST)
    assert not boras.covered
    assert boras.radars[0].name == "Vara" and not boras.radars[0].online
    assert boras.offline == ["Vara"]
    assert coverage(status, *JONKOPING, NEWEST).covered
    assert coverage(status, *STOCKHOLM, NEWEST).covered


def test_all_online_and_unknown_radars() -> None:
    """With every radar delivering the location is covered; a missing radar counts as offline."""
    status = parse_status(radar_status())
    assert coverage(status, *BORAS, NEWEST).covered
    status.pop("vara")
    assert not coverage(status, *BORAS, NEWEST).covered
    late = datetime(2026, 10, 24, 16, 0, tzinfo=UTC)  # 45 minutes without data
    assert not coverage(parse_status(radar_status()), *BORAS, late).covered
    with pytest.raises(SmhiRadarDataError):
        parse_status({"areas": "nope"})
