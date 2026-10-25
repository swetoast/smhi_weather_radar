"""Tests for precipitation type from the snow1g forecast."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.smhi_radar.exceptions import SmhiRadarDataError
from custom_components.smhi_radar.forecast import classify, expected_type, outlook, parse_forecast

from .conftest import forecast_payload

NOW = datetime(2026, 10, 24, 15, 20, tzinfo=UTC)


def test_parse_handles_missing_values() -> None:
    """-9 becomes None; codes and frozen share are read."""
    steps = parse_forecast(forecast_payload())
    assert len(steps) == 48
    dry = steps[0]
    assert (dry.type_code, dry.frozen_part) == (0, None)
    mixed = next(s for s in steps if s.start.hour == 15)
    assert (mixed.type_code, mixed.frozen_part) == (7, 50)
    with pytest.raises(SmhiRadarDataError):
        parse_forecast({"timeSeries": "nope"})


def test_classify_radar_decides_wet_forecast_decides_kind() -> None:
    """Dry radar is none whatever the forecast; wet radar takes the forecast type."""
    steps = parse_forecast(forecast_payload())
    assert classify(steps, NOW, wet=False).state == "none"
    assert classify(steps, NOW, wet=False).forecast_type == "rain_and_snow"
    assert classify(steps, NOW, wet=True).state == "rain_and_snow"
    assert classify(steps, NOW, wet=None).state is None
    # No forecast at all: wet but unknown kind.
    assert classify([], NOW, wet=True).state is None


def test_classify_fallback_when_forecast_is_dry() -> None:
    """Radar wet but forecast dry: use the frozen probability."""
    payload = forecast_payload({})
    for entry in payload["timeSeries"]:
        entry["data"]["probability_of_frozen_precipitation"] = 80.0
    steps = parse_forecast(payload)
    assert classify(steps, NOW, wet=True).state == "snow"
    for entry in payload["timeSeries"]:
        entry["data"]["probability_of_frozen_precipitation"] = 10.0
    assert classify(parse_forecast(payload), NOW, wet=True).state == "rain"


def test_expected_type_follows_eta() -> None:
    """The type is taken at the time the nowcast expects precipitation."""
    steps = parse_forecast(forecast_payload())
    assert expected_type(steps, NOW, 20) == "rain_and_snow"
    assert expected_type(steps, NOW, 50) == "snow"
    assert expected_type(steps, NOW, None) is None
    assert expected_type(steps, NOW + timedelta(days=5), 0) is None


def test_outlook_thunder_amounts_and_arrival_beyond_radar() -> None:
    """Thunder risk, 24 h totals (partial steps prorated) and the first likely step."""
    steps = parse_forecast(forecast_payload())
    future = outlook(steps, NOW, after=NOW + timedelta(minutes=60))
    assert future.thunder == 2
    assert future.thunder_max == 30
    assert future.thunder_max_at == datetime(2026, 10, 24, 18, tzinfo=UTC)
    # Wet steps start 15, 16 and 17 UTC; from 15:20 two thirds of the first counts.
    assert future.amount == round(0.4 * (40 / 60) + 0.4 + 0.4, 1)
    assert future.probability_max == 90
    # The radar looked to 16:20; the 16:00 step is still wet, so precipitation from 16:20.
    assert future.arrival == NOW + timedelta(minutes=60)
    assert future.arrival_probability == 90

    after_rain = outlook(steps, NOW, after=datetime(2026, 10, 24, 18, tzinfo=UTC))
    assert after_rain.arrival is None  # dry for the rest of the day
    assert outlook(steps, NOW, after=None).arrival is None  # radar already expects it
    assert outlook([], NOW, after=NOW).amount is None


def test_expected_in_hands_over_to_forecast(freezer) -> None:
    """With no radar arrival the sensor counts down to the forecast step instead."""
    from types import SimpleNamespace

    from custom_components.smhi_radar.forecast import Outlook
    from custom_components.smhi_radar.nowcast import Nowcast
    from custom_components.smhi_radar.sensor import _minutes_until, _nowcast_attributes

    freezer.move_to(NOW)
    later = NOW + timedelta(hours=3)
    data = SimpleNamespace(
        analysis=SimpleNamespace(nowcast=Nowcast("no_rain", valid=NOW)),
        outlook=Outlook(arrival=later, arrival_probability=80, arrival_amount=1.2),
        expected_type="rain",
        measured=True,
    )
    assert _minutes_until(data) == 180
    attributes = _nowcast_attributes(data)
    assert (attributes["source"], attributes["forecast_probability"]) == ("forecast", 80)

    data.analysis.nowcast = Nowcast("ok", valid=NOW, expected_in=12)
    assert _minutes_until(data) == 12
    assert _nowcast_attributes(data)["source"] == "radar"
