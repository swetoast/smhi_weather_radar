"""Options flow for SMHI Weather Radar."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    LocationSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .analysis import in_coverage
from .const import (
    CONF_ANIMATE,
    CONF_LOCAL_RADIUS,
    CONF_LOCATION,
    CONF_NEARBY_RADIUS,
    CONF_PERSONS,
    CONF_STALE_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_USE_HOME_LOCATION,
    DEFAULT_ANIMATE,
    DEFAULT_LOCAL_RADIUS,
    DEFAULT_NEARBY_RADIUS,
    DEFAULT_STALE_THRESHOLD,
    DEFAULT_UPDATE_INTERVAL,
    MAX_LOCAL_RADIUS,
    MAX_NEARBY_RADIUS,
    MAX_STALE_THRESHOLD,
    MAX_UPDATE_INTERVAL,
    MIN_LOCAL_RADIUS,
    MIN_NEARBY_RADIUS,
    MIN_STALE_THRESHOLD,
    MIN_UPDATE_INTERVAL,
)


def _number(minimum: int, maximum: int, unit: str) -> NumberSelector:
    """Return a whole number input."""
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


class SmhiRadarOptionsFlow(OptionsFlowWithReload):
    """Manage SMHI Weather Radar options; the entry reloads on save."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._pending: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Configure update timing and location source."""
        if user_input is not None:
            self._pending = {
                CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                CONF_STALE_THRESHOLD: int(user_input[CONF_STALE_THRESHOLD]),
                CONF_LOCAL_RADIUS: int(user_input[CONF_LOCAL_RADIUS]),
                CONF_NEARBY_RADIUS: int(user_input[CONF_NEARBY_RADIUS]),
                CONF_PERSONS: list(user_input.get(CONF_PERSONS, [])),
                CONF_ANIMATE: bool(user_input[CONF_ANIMATE]),
                CONF_USE_HOME_LOCATION: bool(user_input[CONF_USE_HOME_LOCATION]),
            }
            if self._pending[CONF_USE_HOME_LOCATION]:
                return self.async_create_entry(data=self._pending)
            return await self.async_step_location()

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): _number(MIN_UPDATE_INTERVAL, MAX_UPDATE_INTERVAL, "min"),
                vol.Required(
                    CONF_STALE_THRESHOLD,
                    default=current.get(CONF_STALE_THRESHOLD, DEFAULT_STALE_THRESHOLD),
                ): _number(MIN_STALE_THRESHOLD, MAX_STALE_THRESHOLD, "min"),
                vol.Required(
                    CONF_LOCAL_RADIUS,
                    default=current.get(CONF_LOCAL_RADIUS, DEFAULT_LOCAL_RADIUS),
                ): _number(MIN_LOCAL_RADIUS, MAX_LOCAL_RADIUS, "km"),
                vol.Required(
                    CONF_NEARBY_RADIUS,
                    default=current.get(CONF_NEARBY_RADIUS, DEFAULT_NEARBY_RADIUS),
                ): _number(MIN_NEARBY_RADIUS, MAX_NEARBY_RADIUS, "km"),
                vol.Optional(
                    CONF_PERSONS,
                    default=current.get(CONF_PERSONS, []),
                ): EntitySelector(EntitySelectorConfig(domain="person", multiple=True)),
                vol.Required(
                    CONF_ANIMATE,
                    default=current.get(CONF_ANIMATE, DEFAULT_ANIMATE),
                ): bool,
                vol.Required(
                    CONF_USE_HOME_LOCATION,
                    default=current.get(CONF_USE_HOME_LOCATION, True),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a custom location inside SMHI radar coverage."""
        errors: dict[str, str] = {}
        if user_input is not None:
            location = user_input[CONF_LOCATION]
            latitude = float(location[CONF_LATITUDE])
            longitude = float(location[CONF_LONGITUDE])
            if in_coverage(latitude, longitude):
                return self.async_create_entry(
                    data={**self._pending, CONF_LATITUDE: latitude, CONF_LONGITUDE: longitude}
                )
            errors[CONF_LOCATION] = "outside_coverage"

        current = self.config_entry.options
        default = {
            CONF_LATITUDE: current.get(CONF_LATITUDE, self.hass.config.latitude),
            CONF_LONGITUDE: current.get(CONF_LONGITUDE, self.hass.config.longitude),
        }
        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {vol.Required(CONF_LOCATION, default=default): LocationSelector()}
            ),
            errors=errors,
            last_step=True,
        )
