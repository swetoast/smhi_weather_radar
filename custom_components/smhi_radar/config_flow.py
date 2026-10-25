"""Config flow for SMHI Weather Radar."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.core import callback

from .const import DOMAIN, NAME
from .options import SmhiRadarOptionsFlow


class SmhiRadarConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure SMHI Weather Radar."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle setup from the integrations page."""
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SmhiRadarOptionsFlow:
        """Create the options flow."""
        return SmhiRadarOptionsFlow()
