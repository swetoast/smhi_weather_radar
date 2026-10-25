"""Integration tests: setup, history, outages, options and people."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from PIL import Image

from homeassistant.components.image import async_get_image
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.smhi_radar.const import API_URL, DOMAIN, RADAR_STATUS_URL, WARNINGS_URL

PREFIX = "smhi_weather_radar"


def state(hass: HomeAssistant, entity: str) -> str:
    return hass.states.get(f"{entity.split('.')[0]}.{PREFIX}_{entity.split('.')[1]}").state


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="SMHI Weather Radar", data={}, options=options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_history_and_sensors(hass: HomeAssistant, smhi_api) -> None:
    """Frames come by dated URL, a missing frame is tolerated, sensors fill in."""
    entry = await _setup(hass)
    data = entry.runtime_data.data

    assert all("latest.png" not in str(call[1]) for call in smhi_api.mock_calls)
    assert data.frame_count == 12  # 13 wanted, one returned 404
    assert state(hass, "sensor.precipitation_intensity") == "0.0"
    assert state(hass, "binary_sensor.precipitation_nearby") == "off"
    assert state(hass, "binary_sensor.radar_data_current") == "on"
    assert 9 <= float(state(hass, "sensor.nearest_precipitation_distance")) <= 14
    assert state(hass, "sensor.nearest_precipitation_direction") == "w"
    assert 10 <= int(state(hass, "sensor.precipitation_expected_in")) <= 30
    assert float(state(hass, "sensor.highest_intensity_next_hour")) > 0
    assert state(hass, "sensor.precipitation_type") == "none"  # dry at home
    expected = hass.states.get(f"sensor.{PREFIX}_precipitation_expected_in")
    assert expected.attributes["expected_type"] in ("rain_and_snow", "snow")
    assert expected.attributes["source"] == "radar"
    assert state(hass, "sensor.thunder_probability") == "2"
    assert float(state(hass, "sensor.precipitation_next_24_hours")) > 0
    warning = hass.states.get(f"sensor.{PREFIX}_weather_warning")
    assert warning.state == "yellow"
    assert [w["event"] for w in warning.attributes["warnings"]] == ["rain"]
    assert state(hass, "binary_sensor.radar_coverage") == "on"

    for entity in ("image.weather_radar", "image.local_weather_radar"):
        image = await async_get_image(hass, f"image.{PREFIX}_{entity.split('.')[1]}")
        assert image.content.startswith(b"\x89PNG")


async def test_expected_in_counts_down(hass: HomeAssistant, smhi_api, freezer) -> None:
    """Between radar frames the minutes count down from the arrival time."""
    await _setup(hass)
    entity = f"sensor.{PREFIX}_precipitation_expected_in"
    first = int(hass.states.get(entity).state)
    assert hass.states.get(entity).attributes["arrival"]
    assert hass.states.get(entity).attributes["motion_pairs"] >= 3

    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert int(hass.states.get(entity).state) == first - 4


async def test_outage_keeps_last_frame(hass: HomeAssistant, smhi_api, freezer) -> None:
    """A failing SMHI keeps values; freshness turns off with age."""
    await _setup(hass)
    smhi_api.clear_requests()
    smhi_api.get(API_URL, status=500)
    smhi_api.get(WARNINGS_URL, status=500)
    smhi_api.get(RADAR_STATUS_URL, status=500)

    freezer.tick(timedelta(minutes=20))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert state(hass, "sensor.precipitation_intensity") == "0.0"
    assert state(hass, "binary_sensor.radar_data_current") == "off"
    assert 9 <= float(state(hass, "sensor.nearest_precipitation_distance")) <= 14


async def test_home_outside_coverage_stops_retrying(hass: HomeAssistant, smhi_api) -> None:
    """A location outside coverage is a setup error, not an endless retry."""
    hass.config.latitude, hass.config.longitude = 48.85, 2.35
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_options_people_and_animation(hass: HomeAssistant, smhi_api, freezer) -> None:
    """Options save, the entry reloads, animation and person markers work."""
    hass.states.async_set("person.toast", "not_home", {"latitude": 57.79, "longitude": 13.42})
    entry = await _setup(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "update_interval": 5,
            "stale_threshold": 15,
            "local_radius": 80,
            "nearby_radius": 25,
            "persons": ["person.toast"],
            "animate": True,
            "use_home_location": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    local = await async_get_image(hass, f"image.{PREFIX}_local_weather_radar")
    assert Image.open(BytesIO(local.content)).n_frames == 12
    assert state(hass, "binary_sensor.precipitation_nearby") == "on"  # 25 km now
    assert state(hass, "sensor.precipitation_type") == "rain_and_snow"

    # Moving the person re-renders without a new SMHI poll.
    coordinator = entry.runtime_data
    before = coordinator.data
    polls = len(smhi_api.mock_calls)
    freezer.tick(timedelta(seconds=1))
    hass.states.async_set("person.toast", "not_home", {"latitude": 57.60, "longitude": 12.70})
    await hass.async_block_till_done()
    assert coordinator.data.rendered > before.rendered
    assert coordinator.data.local_image != before.local_image
    assert len(smhi_api.mock_calls) == polls

    # A second move within the cooldown is held back, then applied.
    freezer.tick(timedelta(seconds=1))
    hass.states.async_set("person.toast", "not_home", {"latitude": 57.65, "longitude": 12.80})
    await hass.async_block_till_done()
    held = coordinator.data.local_image
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert coordinator.data.local_image != held


async def test_custom_location_validation(hass: HomeAssistant, smhi_api) -> None:
    """Custom locations outside coverage are rejected, valid ones saved."""
    from custom_components.smhi_radar.const import FORECAST_URL

    from .conftest import forecast_payload

    smhi_api.get(FORECAST_URL.format(lat=57.75, lon=12.95), json=forecast_payload())
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "update_interval": 5,
            "stale_threshold": 15,
            "local_radius": 100,
            "nearby_radius": 10,
            "animate": False,
            "use_home_location": False,
        },
    )
    assert result["step_id"] == "location"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"location": {"latitude": 48.85, "longitude": 2.35}}
    )
    assert result["errors"] == {"location": "outside_coverage"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"location": {"latitude": 57.75, "longitude": 12.95}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_forecast_outage_does_not_affect_radar(hass: HomeAssistant, smhi_api) -> None:
    """Without forecast and warnings the radar works; those sensors are just unknown."""
    from custom_components.smhi_radar.const import FORECAST_URL

    smhi_api.clear_requests()
    from .conftest import HOME, cell_frame, frame_times, metadata
    from custom_components.smhi_radar.api import frame_url

    smhi_api.get(API_URL, json=metadata())
    smhi_api.get(FORECAST_URL.format(lat=HOME[0], lon=HOME[1]), status=503)
    smhi_api.get(WARNINGS_URL, status=503)
    smhi_api.get(RADAR_STATUS_URL, status=503)
    for valid in frame_times():
        smhi_api.get(frame_url(f"radar_{valid:%y%m%d%H%M}", valid), content=cell_frame(valid))
    await _setup(hass, nearby_radius=25)

    assert state(hass, "binary_sensor.precipitation_nearby") == "on"
    assert state(hass, "sensor.precipitation_type") == "unknown"
    assert state(hass, "sensor.precipitation_intensity") == "0.0"
    assert state(hass, "sensor.weather_warning") == "unknown"
    assert state(hass, "sensor.thunder_probability") == "unknown"


async def test_offline_radar_is_not_dry_weather(hass: HomeAssistant, smhi_api) -> None:
    """Vara stale since 22 October: Boras readings are unknown, not dry; the forecast takes over."""
    from .conftest import radar_status

    smhi_api.clear_requests()
    from .conftest import HOME, cell_frame, forecast_payload, frame_times, metadata, warnings_payload
    from custom_components.smhi_radar.api import frame_url
    from custom_components.smhi_radar.const import FORECAST_URL

    smhi_api.get(API_URL, json=metadata())
    smhi_api.get(FORECAST_URL.format(lat=HOME[0], lon=HOME[1]), json=forecast_payload())
    smhi_api.get(WARNINGS_URL, json=warnings_payload())
    smhi_api.get(RADAR_STATUS_URL, json=radar_status({"vara": "2026-10-22T08:09:56Z"}))
    for valid in frame_times():
        smhi_api.get(frame_url(f"radar_{valid:%y%m%d%H%M}", valid), content=cell_frame(valid))
    await _setup(hass)

    coverage = hass.states.get(f"binary_sensor.{PREFIX}_radar_coverage")
    assert coverage.state == "off"
    assert coverage.attributes["offline_radars"] == ["Vara"]
    for entity in (
        "sensor.precipitation_intensity", "sensor.nearest_precipitation_distance",
        "sensor.highest_intensity_next_hour", "sensor.precipitation_type",
        "binary_sensor.precipitation_nearby",
    ):
        assert state(hass, entity) == "unknown", entity
    expected = hass.states.get(f"sensor.{PREFIX}_precipitation_expected_in")
    assert expected.attributes["status"] == "no_coverage"
    assert expected.attributes["source"] == "forecast"
    assert expected.state == "0"  # the forecast has precipitation in the current hour
    for entity in ("image.weather_radar", "image.local_weather_radar"):
        assert hass.states.get(f"image.{PREFIX}_{entity.split('.')[1]}") is not None
