"""Binary sensor platform for SMHI Weather Radar."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SmhiRadarConfigEntry
from .const import CONF_STALE_THRESHOLD, DEFAULT_STALE_THRESHOLD
from .coordinator import SmhiRadarCoordinator
from .entity import SmhiRadarEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmhiRadarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities([SmhiRadarCurrent(coordinator), SmhiPrecipitationNearby(coordinator)])


class SmhiRadarCurrent(SmhiRadarEntity, BinarySensorEntity):
    """On while the shown radar frame is recent enough."""

    _attr_translation_key = "radar_current"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SmhiRadarCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "radar_current")

    @property
    def is_on(self) -> bool:
        """Return True if the frame is newer than the stale threshold."""
        threshold = self.coordinator.config_entry.options.get(
            CONF_STALE_THRESHOLD, DEFAULT_STALE_THRESHOLD
        )
        age = dt_util.utcnow() - self.coordinator.data.metadata.valid
        return age <= timedelta(minutes=threshold)


class SmhiPrecipitationNearby(SmhiRadarEntity, BinarySensorEntity):
    """On (wet) while there is precipitation within the nearby radius."""

    _attr_translation_key = "precipitation_nearby"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator: SmhiRadarCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "precipitation_nearby")

    @property
    def is_on(self) -> bool | None:
        """Return True if it rains within the radius; None without data."""
        return self.coordinator.data.location.raining_nearby

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return where the answer came from."""
        return {"source": self.coordinator.data.location.nearby_source}
