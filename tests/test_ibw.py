"""Tests for SMHI impact-based warnings."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.smhi_radar.exceptions import SmhiRadarDataError
from custom_components.smhi_radar.ibw import covers, for_location, highest_level, parse_warnings

from .conftest import HOME, warnings_payload

NOW = datetime(2026, 10, 24, 15, 20, tzinfo=UTC)


def test_only_precipitation_warnings_over_the_location() -> None:
    """Wind is ignored, thunder elsewhere is ignored, rain over home is kept."""
    warnings = parse_warnings(warnings_payload())
    assert {w.event for w in warnings} == {"rain", "thunder"}
    here = for_location(warnings, *HOME, NOW)
    assert [(w.event, w.level) for w in here] == [("rain", "yellow")]
    assert here[0].text["en"] == "Heavy rain"
    assert highest_level(here) == "yellow"
    assert highest_level([]) == "none"
    # Ended warnings drop out.
    assert for_location(warnings, *HOME, datetime(2026, 10, 26, tzinfo=UTC)) == []
    with pytest.raises(SmhiRadarDataError):
        parse_warnings({"not": "a list"})


def test_polygon_holes_and_multipolygons() -> None:
    """A hole excludes the point; any part of a MultiPolygon includes it."""
    outer = [[12.0, 57.0], [14.0, 57.0], [14.0, 58.5], [12.0, 58.5], [12.0, 57.0]]
    hole = [[12.8, 57.6], [13.1, 57.6], [13.1, 57.8], [12.8, 57.8], [12.8, 57.6]]
    far = [[20.0, 65.0], [21.0, 65.0], [21.0, 66.0], [20.0, 65.0]]
    feed = warnings_payload()[:1]
    feed[0]["warningAreas"][0]["area"]["geometry"] = {"type": "Polygon", "coordinates": [outer, hole]}
    assert not covers(parse_warnings(feed)[0], *HOME)
    feed[0]["warningAreas"][0]["area"]["geometry"] = {"type": "MultiPolygon", "coordinates": [[far], [outer]]}
    assert covers(parse_warnings(feed)[0], *HOME)
