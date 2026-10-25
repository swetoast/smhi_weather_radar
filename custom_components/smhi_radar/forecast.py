"""Precipitation type from the SMHI snow1g point forecast.

Radar shows whether and how hard it is precipitating, but not whether it
is rain or snow. The forecast fills that gap. Only precipitation fields are
read; nothing from here is drawn on the map.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .const import PRECIPITATION_TYPES, TYPE_NONE
from .exceptions import SmhiRadarDataError

_MISSING = -9  # snow1g value for "not defined" (for example no precipitation)


@dataclass(frozen=True, slots=True)
class PrecipitationStep:
    """Forecast precipitation for one interval."""

    start: datetime
    end: datetime
    type_code: int | None
    frozen_part: int | None
    probability_frozen: float | None


@dataclass(frozen=True, slots=True)
class PrecipitationType:
    """Precipitation type at the location, combining radar and forecast."""

    state: str | None
    forecast_type: str | None
    frozen_part: int | None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _number(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if not isinstance(value, (int, float)) or value == _MISSING:
        return None
    return float(value)


def parse_forecast(payload: Any) -> list[PrecipitationStep]:
    """Return precipitation steps from a snow1g point response."""
    if not isinstance(payload, dict) or not isinstance(payload.get("timeSeries"), list):
        raise SmhiRadarDataError("SMHI forecast has no timeSeries")
    steps = []
    for entry in payload["timeSeries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
            continue
        end = _time(entry.get("time"))
        start = _time(entry.get("intervalParametersStartTime"))
        if start is None or end is None:
            continue
        data = entry["data"]
        type_code = _number(data, "predominant_precipitation_type_at_surface")
        frozen = _number(data, "precipitation_frozen_part")
        steps.append(
            PrecipitationStep(
                start=start,
                end=end,
                type_code=None if type_code is None else int(type_code),
                frozen_part=None if frozen is None else round(frozen),
                probability_frozen=_number(data, "probability_of_frozen_precipitation"),
            )
        )
    if not steps:
        raise SmhiRadarDataError("SMHI forecast has no usable steps")
    return sorted(steps, key=lambda step: step.start)


def _step_at(steps: list[PrecipitationStep], moment: datetime) -> PrecipitationStep | None:
    for step in steps:
        if step.start <= moment < step.end:
            return step
    return None


def _type_name(step: PrecipitationStep | None) -> str | None:
    """Return the forecast type, using the frozen probability as fallback."""
    if step is None:
        return None
    if step.type_code:
        return PRECIPITATION_TYPES.get(step.type_code)
    if step.probability_frozen is not None:
        # Forecast says dry but the radar sees precipitation.
        return "snow" if step.probability_frozen >= 50 else "rain"
    return None


def classify(
    steps: list[PrecipitationStep], now: datetime, wet: bool | None
) -> PrecipitationType:
    """Return the type now: radar decides if it is wet, the forecast what kind."""
    step = _step_at(steps, now)
    forecast_type = (
        None
        if step is None or step.type_code is None
        else PRECIPITATION_TYPES.get(step.type_code, TYPE_NONE)
    )
    frozen = step.frozen_part if step else None
    if wet is None:
        return PrecipitationType(None, forecast_type, frozen)
    if not wet:
        return PrecipitationType(TYPE_NONE, forecast_type, frozen)
    return PrecipitationType(_type_name(step), forecast_type, frozen)


def expected_type(
    steps: list[PrecipitationStep], now: datetime, minutes: int | None
) -> str | None:
    """Return the forecast type when the nowcast expects precipitation."""
    if minutes is None:
        return None
    return _type_name(_step_at(steps, now + timedelta(minutes=minutes)))
