"""Shared fixtures for SMHI Weather Radar tests.

The fixture world: home in Boras, a rain cell 20 km west of home moving
east at 36 km/h, and an hour of frames published every five minutes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO

import numpy as np
from PIL import Image
import pytest

from custom_components.smhi_radar.analysis import Grid
from custom_components.smhi_radar.api import frame_url

NOW = datetime(2026, 10, 24, 15, 20, tzinfo=UTC)
NEWEST = datetime(2026, 10, 24, 15, 15, tzinfo=UTC)
HOME = (57.72, 12.94)
WIDTH, HEIGHT = 471, 887
GRID = Grid(WIDTH, HEIGHT)
SPEED_PX_PER_MIN = 0.3  # 0.3 * 2.015 km * 60 = about 36 km/h
LIGHT = (10, 208, 10)  # 20 dBZ, 0.63 mm/h
HEAVY = (255, 200, 0)  # 30 dBZ, 2.9 mm/h


def frame_times() -> list[datetime]:
    """Return the hour of frame times, oldest first."""
    return [NEWEST - timedelta(minutes=5 * step) for step in range(12, -1, -1)]


def cell_frame(valid: datetime, rain_at_home: bool = False) -> bytes:
    """Return a frame with the moving cell (and optionally rain at home)."""
    home_x, home_y = GRID.to_pixel(*HOME)
    minutes = (valid - NEWEST).total_seconds() / 60
    centre_x = home_x - 10 + SPEED_PX_PER_MIN * minutes
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    distance = np.hypot(xx + 0.5 - centre_x, yy + 0.5 - home_y)
    rgba = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
    rgba[distance <= 4] = (*LIGHT, 255)
    rgba[distance <= 2] = (*HEAVY, 255)
    if rain_at_home:
        rgba[int(home_y), int(home_x)] = (*LIGHT, 255)
    output = BytesIO()
    Image.fromarray(rgba, "RGBA").save(output, "PNG")
    return output.getvalue()


def metadata(valid: datetime = NEWEST) -> dict:
    """Return a status response for a newest frame."""
    key = f"radar_{valid:%y%m%d%H%M}"
    return {
        "key": "comp",
        "updated": (valid + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeZone": "UTC",
        "lastFiles": [
            {
                "key": key,
                "valid": valid.strftime("%Y-%m-%d %H:%M"),
                "updated": (valid + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "formats": [
                    {
                        "key": "png",
                        "updated": (valid + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "link": frame_url("latest", valid).rsplit("/", 4)[0] + "/latest.png",
                    }
                ],
            }
        ],
    }


def forecast_payload(codes: dict[int, int] | None = None) -> dict:
    """Return a snow1g response with hourly steps from 14:00 UTC.

    codes maps the UTC start hour to a precipitation type code; other hours
    are dry (0, frozen part -9).
    """
    codes = codes if codes is not None else {15: 7, 16: 5, 17: 5}
    start = datetime(2026, 10, 24, 14, tzinfo=UTC)
    series = []
    for hour in range(48):
        begin = start + timedelta(hours=hour)
        code = codes.get(begin.hour if hour < 24 else -1, 0)
        series.append(
            {
                "time": (begin + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "intervalParametersStartTime": begin.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "data": {
                    "air_temperature": 0.5,
                    "precipitation_amount_mean": 0.4 if code else 0.0,
                    "precipitation_amount_min": 0.1 if code else 0.0,
                    "precipitation_amount_max": 0.9 if code else 0.0,
                    "probability_of_precipitation": 90 if code else 5,
                    "thunderstorm_probability": 30 if begin.hour == 18 and hour < 24 else 2,
                    "probability_of_frozen_precipitation": 90.0 if code in (5, 6, 7) else 0.0,
                    "precipitation_frozen_part": {5: 100, 6: 80, 7: 50}.get(code, -9),
                    "predominant_precipitation_type_at_surface": code,
                },
            }
        )
    return {"createdTime": "2026-10-24T15:05:00Z", "referenceTime": "2026-10-24T15:00:00Z", "timeSeries": series}


def _area(level: str, ring: list[list[float]], name: str) -> dict:
    return {
        "approximateStart": "2026-10-24T12:00:00Z",
        "approximateEnd": "2026-10-25T06:00:00Z",
        "areaName": {"sv": name},
        "warningLevel": {"code": level},
        "eventDescription": {"sv": "Mycket regn", "en": "Heavy rain", "code": "RAIN"},
        "area": {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}},
    }


def warnings_payload() -> list:
    """IBW feed: yellow rain over home, orange thunder near Stockholm, wind over home."""
    home = [[12.0, 57.0], [14.0, 57.0], [14.0, 58.5], [12.0, 58.5], [12.0, 57.0]]
    stockholm = [[17.5, 59.0], [18.5, 59.0], [18.5, 59.6], [17.5, 59.6], [17.5, 59.0]]
    return [
        {"id": 1, "event": {"code": "RAIN"}, "warningAreas": [_area("YELLOW", home, "V\u00e4stra G\u00f6talands l\u00e4n")]},
        {"id": 2, "event": {"code": "THUNDER"}, "warningAreas": [_area("ORANGE", stockholm, "Stockholms l\u00e4n")]},
        {"id": 3, "event": {"code": "WIND"}, "warningAreas": [_area("RED", home, "V\u00e4stra G\u00f6talands l\u00e4n")]},
    ]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of custom integrations."""


@pytest.fixture
def smhi_api(hass, aioclient_mock, freezer):
    """Freeze time, set home and serve an hour of frames by dated URL only.

    The frame 20 minutes before the newest is missing (404) on purpose.
    """
    from custom_components.smhi_radar.const import API_URL, FORECAST_URL, WARNINGS_URL

    freezer.move_to(NOW)
    hass.config.latitude, hass.config.longitude = HOME
    aioclient_mock.get(API_URL, json=metadata())
    aioclient_mock.get(FORECAST_URL.format(lat=HOME[0], lon=HOME[1]), json=forecast_payload())
    aioclient_mock.get(WARNINGS_URL, json=warnings_payload())
    for valid in frame_times():
        url = frame_url(f"radar_{valid:%y%m%d%H%M}", valid)
        if valid == NEWEST - timedelta(minutes=20):
            aioclient_mock.get(url, status=404)
        else:
            aioclient_mock.get(url, content=cell_frame(valid))
    return aioclient_mock
