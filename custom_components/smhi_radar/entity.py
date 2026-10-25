"""Base entity for SMHI Weather Radar."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import SmhiRadarCoordinator


class SmhiRadarEntity(CoordinatorEntity[SmhiRadarCoordinator]):
    """Base coordinator entity."""

    _attr_attribution = "Data provided by SMHI"
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmhiRadarCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=NAME,
            manufacturer="SMHI",
            model="Sweden composite radar",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://opendata.smhi.se/apidocs/radar",
        )
