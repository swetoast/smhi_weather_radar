"""Diagnostics for SMHI Weather Radar."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant

from . import SmhiRadarConfigEntry

TO_REDACT = {CONF_LATITUDE, CONF_LONGITUDE, "location", "pixel", "persons"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmhiRadarConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with the location redacted."""
    coordinator = entry.runtime_data
    data = coordinator.data
    radar = None
    if data is not None:
        radar = {
            "key": data.metadata.key,
            "link": data.metadata.link,
            "observation_time": data.metadata.valid.isoformat(),
            "file_updated": data.metadata.file_updated.isoformat(),
            "format_updated": data.metadata.format_updated.isoformat(),
            "api_updated": data.metadata.api_updated.isoformat(),
            "rendered": data.rendered.isoformat(),
            "frames_in_history": data.frame_count,
            "reflectivity_dbz": data.analysis.reading.reflectivity,
            "precipitation_intensity_mm_h": data.analysis.reading.intensity,
            "pixel": [data.analysis.reading.pixel_x, data.analysis.reading.pixel_y],
            "location": [data.latitude, data.longitude],
            "nearby": asdict(data.analysis.nearby),
            "nowcast": {k: v for k, v in asdict(data.analysis.nowcast).items() if k != "local"},
            "values_at_location": asdict(data.location),
            "sources": {
                "reading": data.analysis.reading_source,
                "nearby": data.analysis.nearby_source,
                "location_covered": data.analysis.covered,
            },
            "coverage": asdict(data.coverage) if data.coverage else None,
            "outlook": asdict(data.outlook),
            "warnings": len(data.warnings) if data.warnings is not None else None,
            "precipitation_type": asdict(data.precipitation_type),
            "expected_type": data.expected_type,
            "image_bytes": len(data.image),
            "national_image_bytes": len(data.national_image),
            "local_image_bytes": len(data.local_image),
        }
    return async_redact_data(
        {
            "entry": {"title": entry.title, "options": dict(entry.options)},
            "coordinator": {
                "last_update_success": coordinator.last_update_success,
                "last_error": coordinator.last_error,
                "failed_update_count": coordinator.failed_update_count,
            },
            "radar": radar,
        },
        TO_REDACT,
    )
