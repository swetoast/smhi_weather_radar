"""Image platform for SMHI Weather Radar."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmhiRadarConfigEntry
from .coordinator import SmhiRadarCoordinator
from .entity import SmhiRadarEntity
from .models import RadarData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmhiRadarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the radar images."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            SmhiRadarImage(coordinator, "weather_radar", lambda data: data.national_image),
            SmhiRadarImage(coordinator, "local_radar", lambda data: data.local_image),
        ]
    )


class SmhiRadarImage(SmhiRadarEntity, ImageEntity):
    """Radar map image (PNG, animated PNG when animation is on)."""

    _attr_content_type = "image/png"

    def __init__(
        self,
        coordinator: SmhiRadarCoordinator,
        key: str,
        image_fn: Callable[[RadarData], bytes],
    ) -> None:
        """Initialize the image entity."""
        ImageEntity.__init__(self, coordinator.hass)
        SmhiRadarEntity.__init__(self, coordinator, key)
        self._attr_translation_key = key
        self._image_fn = image_fn

    @property
    def image_last_updated(self) -> datetime | None:
        """Return when the image was rendered; a change makes the frontend reload it."""
        return self.coordinator.data.rendered

    async def async_image(self) -> bytes | None:
        """Return the rendered image."""
        return self._image_fn(self.coordinator.data)
