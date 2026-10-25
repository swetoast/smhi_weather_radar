"""Precipitation context from the SMHI snow1g point forecast.

Radar shows whether and how hard it is precipitating, but not whether it
is rain or snow, and it can only look about an hour ahead. The forecast
fills both gaps: the precipitation type, thunderstorm probability, and
precipitation beyond the radar horizon. Only precipitation fields are
read; nothing from here is drawn on the map.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .const import PRECIPITATION_TYPES, TYPE_NONE
from .exceptions import SmhiRadarDataError

_MISSING = -9  # snow1g value for "not defined" (for example no precipitation)
LIKELY_PROBABILITY = 50  # percent; a step counts as wet from here
THUNDER_HOURS = 6
OUTLOOK_HOURS = 24


@dataclass(frozen=True, slots=True)
class PrecipitationStep:
    """Forecast precipitation for one interval."""

    start: datetime
    end: datetime
    type_code: int | None
    frozen_part: int | None
    probability_frozen: float | None
    thunder: float | None = None  # percent
    probability: float | None = None  # percent chance of at least 0.1 mm
    amount: float | None = None  # mm in the step, ensemble mean
    amount_min: float | None = None
    amount_max: float | None = None

    @property
    def likely_wet(self) -> bool:
        """Return True when precipitation is likely in this step."""
        return (self.probability or 0) >= LIKELY_PROBABILITY and (self.amount or 0) > 0


@dataclass(frozen=True, slots=True)
class Outlook:
    """What the forecast says beyond the radar."""

    thunder: float | None = None
    thunder_max: float | None = None
    thunder_max_at: datetime | None = None
    amount: float | None = None
    amount_min: float | None = None
    amount_max: float | None = None
    probability_max: float | None = None
    arrival: datetime | None = None
    arrival_probability: float | None = None
    arrival_amount: float | None = None


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
                thunder=_number(data, "thunderstorm_probability"),
                probability=_number(data, "probability_of_precipitation"),
                amount=_number(data, "precipitation_amount_mean"),
                amount_min=_number(data, "precipitation_amount_min"),
                amount_max=_number(data, "precipitation_amount_max"),
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


def _overlap(step: PrecipitationStep, start: datetime, end: datetime) -> float:
    """Return the share of the step inside [start, end)."""
    length = (step.end - step.start).total_seconds()
    inside = (min(step.end, end) - max(step.start, start)).total_seconds()
    return max(0.0, inside) / length if length > 0 else 0.0


def outlook(steps: list[PrecipitationStep], now: datetime, after: datetime | None) -> Outlook:
    """Return thunder risk, the next 24 hours and the first likely precipitation.

    after is where the search for precipitation starts: the end of the radar
    nowcast, or now when the radar cannot tell. None skips the search
    (the radar already expects precipitation).
    """
    if not steps:
        return Outlook()
    current = _step_at(steps, now)
    thunder_end = now + timedelta(hours=THUNDER_HOURS)
    thunder = [s for s in steps if s.thunder is not None and _overlap(s, now, thunder_end) > 0]
    peak = max(thunder, key=lambda s: s.thunder, default=None)

    end = now + timedelta(hours=OUTLOOK_HOURS)
    window = [(s, _overlap(s, now, end)) for s in steps]
    window = [(s, share) for s, share in window if share > 0]

    def total(field: str) -> float | None:
        values = [(getattr(s, field), share) for s, share in window]
        if not values or any(value is None for value, _ in values):
            return None
        return round(sum(value * share for value, share in values), 1)

    arrival_step = None
    if after is not None:
        arrival_step = next((s for s in steps if s.end > after and s.start < end and s.likely_wet), None)
    return Outlook(
        thunder=current.thunder if current else None,
        thunder_max=peak.thunder if peak else None,
        thunder_max_at=max(peak.start, now) if peak else None,
        amount=total("amount"),
        amount_min=total("amount_min"),
        amount_max=total("amount_max"),
        probability_max=max((s.probability for s, _ in window if s.probability is not None), default=None),
        arrival=max(arrival_step.start, after) if arrival_step and after else None,
        arrival_probability=arrival_step.probability if arrival_step else None,
        arrival_amount=arrival_step.amount if arrival_step else None,
    )
