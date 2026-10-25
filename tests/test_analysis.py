"""Tests for projection, decoding and nearby analysis."""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image
import pytest

from custom_components.smhi_radar.analysis import (
    NO_DATA,
    NO_ECHO,
    RGB_TO_DBZ,
    decode_frame,
    in_coverage,
    nearby_rain,
    rain_rate_from_dbz,
    rain_rate_grid,
    read_point,
)
from custom_components.smhi_radar.projection import to_sweref99tm

from .conftest import GRID, HOME, NEWEST, cell_frame


def test_projection_matches_pyproj() -> None:
    """Reference values from pyproj (EPSG:4326 to EPSG:3006)."""
    easting, northing = to_sweref99tm(*HOME)
    assert easting == pytest.approx(377297.363309, abs=0.001)
    assert northing == pytest.approx(6399402.799625, abs=0.001)
    assert in_coverage(*HOME)
    assert not in_coverage(48.85, 2.35)


def test_colour_scale_and_decoding() -> None:
    """Every SMHI colour decodes; transparent is no echo, others no data."""
    assert sorted(RGB_TO_DBZ.values()) == list(range(-29, 71))
    assert rain_rate_from_dbz(4) == 0.0
    assert rain_rate_from_dbz(20) == pytest.approx(0.63, abs=0.01)
    assert rain_rate_from_dbz(40) == pytest.approx(14, abs=0.5)

    pixels = [(0, 0, 0, 0), (10, 208, 10, 255), (255, 255, 255, 255), (54, 54, 54, 255)]
    image = Image.new("RGBA", (4, 1))
    image.putdata(pixels)
    output = BytesIO()
    image.save(output, "PNG")
    _, dbz = decode_frame(output.getvalue())
    assert dbz.tolist() == [[NO_ECHO, 20, NO_DATA, -29]]


def test_point_and_nearby() -> None:
    """The cell 20 km west: dry at home, nearest rain about 12 km west."""
    _, dbz = decode_frame(cell_frame(NEWEST))
    reading = read_point(dbz, GRID, *HOME)
    assert reading.intensity == 0.0
    assert (reading.pixel_x, reading.pixel_y) == (124, 680)

    rate = rain_rate_grid(dbz)
    near = nearby_rain(rate, GRID, *HOME, radius_km=10, search_km=100)
    assert near.raining is False
    assert near.max_intensity == 0.0
    assert 9 <= near.nearest_km <= 14
    assert near.nearest_direction == "w"

    wide = nearby_rain(rate, GRID, *HOME, radius_km=25, search_km=100)
    assert wide.raining is True
    assert wide.max_intensity == pytest.approx(rain_rate_from_dbz(30), abs=0.01)


def test_rain_at_home_is_zero_distance() -> None:
    """Rain in the location's own pixel is 0 km away."""
    _, dbz = decode_frame(cell_frame(NEWEST, rain_at_home=True))
    near = nearby_rain(rain_rate_grid(dbz), GRID, *HOME, radius_km=10, search_km=100)
    assert near.raining is True
    assert near.nearest_km == 0.0
    assert np.isfinite(near.max_intensity)
