"""Sensor platform for SMHI Weather Radar.

Location values come from data.location, which already combines the radar
(measured, or traced into a radar gap) with the SMHI forecast where the
radar cannot see. Each such sensor has a "source" attribute.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfTime,
    UnitOfVolumetricFlux,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import SmhiRadarConfigEntry
from .analysis import COMPASS
from .const import PRECIPITATION_TYPES, TYPE_NONE
from .coordinator import SmhiRadarCoordinator
from .coverage import STATES as COVERAGE_STATES
from .entity import SmhiRadarEntity
from .ibw import LEVELS as WARNING_LEVELS, highest_level
from .models import RadarData

PARALLEL_UPDATES = 0


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _minutes_until(data: RadarData) -> int | None:
    arrival = data.location.arrival
    if arrival is None:
        return None
    return max(0, math.ceil((arrival - dt_util.utcnow()).total_seconds() / 60))


def _arrival_attributes(data: RadarData) -> dict[str, Any]:
    nowcast, location = data.analysis.nowcast, data.location
    forecast = location.arrival_source == "forecast"
    return {
        "source": location.arrival_source,
        "arrival": _iso(location.arrival),
        "status": nowcast.status,
        "radar_time": _iso(nowcast.valid),
        "radar_horizon_minutes": nowcast.horizon,
        "forecast_probability": data.outlook.arrival_probability if forecast else None,
        "forecast_amount": data.outlook.arrival_amount if forecast else None,
        "motion_pairs": nowcast.pairs_used,
        "motion_speed_kmh": nowcast.speed_kmh,
        "motion_heading": nowcast.heading,
        "motion_heading_direction": nowcast.heading_direction,
        "expected_type": data.expected_type,
        "forecast": nowcast.forecast,
    }


def _warning_attributes(data: RadarData, language: str) -> dict[str, Any]:
    lang = "sv" if language.startswith("sv") else "en"
    return {
        "warnings": [
            {
                "event": warning.event,
                "level": warning.level,
                "description": warning.text.get(lang) or warning.text.get("sv"),
                "area": warning.area,
                "start": _iso(warning.start),
                "end": _iso(warning.end),
            }
            for warning in data.warnings or ()
        ]
    }


def _coverage_attributes(data: RadarData) -> dict[str, Any] | None:
    coverage = data.coverage
    if coverage is None:
        return None
    return {
        "offline_radars": coverage.offline,
        "radars": [
            {
                "name": radar.name,
                "distance_km": radar.distance_km,
                "online": radar.online,
                "last_data": _iso(radar.updated),
            }
            for radar in coverage.radars
        ],
    }


@dataclass(frozen=True, kw_only=True)
class SmhiRadarSensorDescription(SensorEntityDescription):
    """Describes an SMHI radar sensor."""

    value_fn: Callable[[RadarData], float | int | str | datetime | None]
    attributes_fn: Callable[..., dict[str, Any] | None] | None = None
    # Recomputed every minute between radar frames (countdowns).
    live: bool = False
    # attributes_fn takes the Home Assistant language as a second argument.
    language_attributes: bool = False


SENSORS: tuple[SmhiRadarSensorDescription, ...] = (
    SmhiRadarSensorDescription(
        key="precipitation_intensity",
        translation_key="precipitation_intensity",
        device_class=SensorDeviceClass.PRECIPITATION_INTENSITY,
        native_unit_of_measurement=UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.location.intensity,
        attributes_fn=lambda data: {"source": data.location.intensity_source},
    ),
    SmhiRadarSensorDescription(
        key="reflectivity",
        translation_key="reflectivity",
        native_unit_of_measurement="dBZ",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.location.reflectivity,
    ),
    SmhiRadarSensorDescription(
        key="observation_time",
        translation_key="observation_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.metadata.valid,
    ),
    SmhiRadarSensorDescription(
        key="nearby_max_intensity",
        translation_key="nearby_max_intensity",
        device_class=SensorDeviceClass.PRECIPITATION_INTENSITY,
        native_unit_of_measurement=UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: None if data.location.nearby_max is None else round(data.location.nearby_max, 2),
        attributes_fn=lambda data: {"source": data.location.nearby_source if data.location.nearby_max is not None else None},
    ),
    SmhiRadarSensorDescription(
        key="nearest_precipitation_distance",
        translation_key="nearest_precipitation_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.location.nearest_km,
    ),
    SmhiRadarSensorDescription(
        key="nearest_precipitation_direction",
        translation_key="nearest_precipitation_direction",
        device_class=SensorDeviceClass.ENUM,
        options=list(COMPASS),
        value_fn=lambda data: data.location.nearest_direction,
        attributes_fn=lambda data: {"bearing": data.location.nearest_bearing},
    ),
    SmhiRadarSensorDescription(
        key="precipitation_type",
        translation_key="precipitation_type",
        device_class=SensorDeviceClass.ENUM,
        options=[TYPE_NONE, *PRECIPITATION_TYPES.values()],
        value_fn=lambda data: data.precipitation_type.state,
        attributes_fn=lambda data: {
            "forecast_type": data.precipitation_type.forecast_type,
            "frozen_part": data.precipitation_type.frozen_part,
        },
    ),
    SmhiRadarSensorDescription(
        key="rain_expected_in",
        translation_key="rain_expected_in",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=_minutes_until,
        attributes_fn=_arrival_attributes,
        live=True,
    ),
    SmhiRadarSensorDescription(
        key="precipitation_next_hour",
        translation_key="precipitation_next_hour",
        device_class=SensorDeviceClass.PRECIPITATION_INTENSITY,
        native_unit_of_measurement=UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.location.next_hour_max,
        attributes_fn=lambda data: {
            "source": data.location.next_hour_source,
            "status": data.analysis.nowcast.status,
        },
    ),
    SmhiRadarSensorDescription(
        key="thunder_probability",
        translation_key="thunder_probability",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: None if data.outlook.thunder is None else round(data.outlook.thunder),
        attributes_fn=lambda data: {
            "max_next_6h": data.outlook.thunder_max,
            "max_next_6h_at": _iso(data.outlook.thunder_max_at),
        },
    ),
    SmhiRadarSensorDescription(
        key="precipitation_next_24h",
        translation_key="precipitation_next_24h",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.outlook.amount,
        attributes_fn=lambda data: {
            "min": data.outlook.amount_min,
            "max": data.outlook.amount_max,
            "max_probability": data.outlook.probability_max,
        },
    ),
    SmhiRadarSensorDescription(
        key="weather_warning",
        translation_key="weather_warning",
        device_class=SensorDeviceClass.ENUM,
        options=list(WARNING_LEVELS),
        value_fn=lambda data: None if data.warnings is None else highest_level(list(data.warnings)),
        attributes_fn=_warning_attributes,
        language_attributes=True,
    ),
    SmhiRadarSensorDescription(
        key="radar_coverage",
        translation_key="radar_coverage",
        device_class=SensorDeviceClass.ENUM,
        options=list(COVERAGE_STATES),
        value_fn=lambda data: None if data.coverage is None else data.coverage.state,
        attributes_fn=_coverage_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmhiRadarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SMHI Weather Radar sensors."""
    async_add_entities(
        SmhiRadarSensor(entry.runtime_data, description) for description in SENSORS
    )


class SmhiRadarSensor(SmhiRadarEntity, SensorEntity):
    """SMHI radar sensor."""

    entity_description: SmhiRadarSensorDescription

    def __init__(
        self, coordinator: SmhiRadarCoordinator, description: SmhiRadarSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Count down between radar frames for live sensors."""
        await super().async_added_to_hass()
        if self.entity_description.live:
            self.async_on_remove(
                async_track_time_interval(self.hass, self._async_tick, timedelta(minutes=1))
            )

    @callback
    def _async_tick(self, now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | int | str | datetime | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        description = self.entity_description
        if description.attributes_fn is None:
            return None
        if description.language_attributes:
            return description.attributes_fn(self.coordinator.data, self.hass.config.language)
        return description.attributes_fn(self.coordinator.data)
