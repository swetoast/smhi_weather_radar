"""SMHI Weather Radar integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmhiRadarClient
from .const import PLATFORMS
from .coordinator import SmhiRadarCoordinator

type SmhiRadarConfigEntry = ConfigEntry[SmhiRadarCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SmhiRadarConfigEntry) -> bool:
    """Set up SMHI Weather Radar from a config entry."""
    coordinator = SmhiRadarCoordinator(
        hass, entry, SmhiRadarClient(async_get_clientsession(hass))
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_track_persons())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmhiRadarConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
