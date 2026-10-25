"""Sensor platform for SMHI Weather Radar."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfLength,
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
from .entity import SmhiRadarEntity
from .models import RadarData

PARALLEL_UPDATES = 0

_NOWCAST_USABLE = ("ok", "no_rain")


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _nowcast_attributes(data: RadarData) -> dict[str, Any]:
    nowcast = data.analysis.nowcast
    arrival = nowcast.arrival
    return {
        "status": nowcast.status,
        "arrival": arrival.isoformat() if arrival else None,
        "radar_time": nowcast.valid.isoformat() if nowcast.valid else None,
        "motion_pairs": nowcast.pairs_used,
        "motion_speed_kmh": nowcast.speed_kmh,
        "motion_heading": nowcast.heading,
        "motion_heading_direction": nowcast.heading_direction,
        "expected_type": data.expected_type,
        "forecast": nowcast.forecast,
    }


@dataclass(frozen=True, kw_only=True)
class SmhiRadarSensorDescription(SensorEntityDescription):
    """Describes an SMHI radar sensor."""

    value_fn: Callable[[RadarData], float | int | str | datetime | None]
    attributes_fn: Callable[[RadarData], dict[str, Any]] | None = None
    # Recomputed every minute between radar frames (countdowns).
    live: bool = False


SENSORS: tuple[SmhiRadarSensorDescription, ...] = (
    SmhiRadarSensorDescription(
        key="precipitation_intensity",
        translation_key="precipitation_intensity",
        device_class=SensorDeviceClass.PRECIPITATION_INTENSITY,
        native_unit_of_measurement=UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: _round(data.analysis.reading.intensity),
    ),
    SmhiRadarSensorDescription(
        key="reflectivity",
        translation_key="reflectivity",
        native_unit_of_measurement="dBZ",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.analysis.reading.reflectivity,
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
        value_fn=lambda data: _round(data.analysis.nearby.max_intensity),
    ),
    SmhiRadarSensorDescription(
        key="nearest_precipitation_distance",
        translation_key="nearest_precipitation_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.analysis.nearby.nearest_km,
    ),
    SmhiRadarSensorDescription(
        key="nearest_precipitation_direction",
        translation_key="nearest_precipitation_direction",
        device_class=SensorDeviceClass.ENUM,
        options=list(COMPASS),
        value_fn=lambda data: data.analysis.nearby.nearest_direction,
        attributes_fn=lambda data: {"bearing": data.analysis.nearby.nearest_bearing},
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
        value_fn=lambda data: data.analysis.nowcast.minutes_until(dt_util.utcnow()),
        attributes_fn=_nowcast_attributes,
        live=True,
    ),
    SmhiRadarSensorDescription(
        key="precipitation_next_hour",
        translation_key="precipitation_next_hour",
        device_class=SensorDeviceClass.PRECIPITATION_INTENSITY,
        native_unit_of_measurement=UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: (
            data.analysis.nowcast.next_hour_max
            if data.analysis.nowcast.status in _NOWCAST_USABLE
            else None
        ),
        attributes_fn=lambda data: {"status": data.analysis.nowcast.status},
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
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
