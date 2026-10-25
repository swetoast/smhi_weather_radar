"""SMHI impact-based weather warnings (IBW) for the location.

The feed lists every current and upcoming warning in Sweden, each with
one or more warning areas carrying a GeoJSON polygon. Only rain, snow and
thunder warnings whose area contains the location are kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .exceptions import SmhiRadarDataError

EVENTS: tuple[str, ...] = ("RAIN", "SNOW", "THUNDER")
LEVELS: tuple[str, ...] = ("none", "message", "yellow", "orange", "red")  # rising severity


@dataclass(frozen=True, slots=True)
class Warning:
    """One warning area that covers the location."""

    event: str
    level: str
    start: datetime | None
    end: datetime | None
    area: str | None
    text: dict[str, str]  # language -> event description
    polygons: tuple[tuple[tuple[tuple[float, float], ...], ...], ...]  # polygons of rings of (lon, lat)


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _polygons(geometry: Any) -> tuple:
    if not isinstance(geometry, dict):
        return ()
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon":
        coordinates = [coordinates]
    elif geometry.get("type") != "MultiPolygon":
        return ()
    try:
        return tuple(
            tuple(tuple((float(x), float(y)) for x, y, *_ in ring) for ring in polygon)
            for polygon in coordinates
        )
    except (TypeError, ValueError):
        return ()


def _inside_ring(ring: tuple[tuple[float, float], ...], lon: float, lat: float) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def covers(warning: Warning, latitude: float, longitude: float) -> bool:
    """Return True when a warning polygon (outer ring minus holes) holds the point."""
    for polygon in warning.polygons:
        if polygon and _inside_ring(polygon[0], longitude, latitude):
            if not any(_inside_ring(hole, longitude, latitude) for hole in polygon[1:]):
                return True
    return False


def parse_warnings(payload: Any) -> list[Warning]:
    """Return rain, snow and thunder warning areas from the IBW feed."""
    if not isinstance(payload, list):
        raise SmhiRadarDataError("SMHI warnings feed is not a list")
    result = []
    for warning in payload:
        if not isinstance(warning, dict):
            continue
        event = ((warning.get("event") or {}).get("code") or "").upper()
        if event not in EVENTS:
            continue
        for area in warning.get("warningAreas") or []:
            if not isinstance(area, dict):
                continue
            level = ((area.get("warningLevel") or {}).get("code") or "").lower()
            polygons = _polygons((area.get("area") or {}).get("geometry"))
            if level not in LEVELS or not polygons:
                continue
            description = area.get("eventDescription") or {}
            result.append(
                Warning(
                    event=event.lower(),
                    level=level,
                    start=_time(area.get("approximateStart")),
                    end=_time(area.get("approximateEnd")),
                    area=(area.get("areaName") or {}).get("sv"),
                    text={k: v for k, v in description.items() if k in ("sv", "en") and isinstance(v, str)},
                    polygons=polygons,
                )
            )
    return result


def for_location(warnings: list[Warning], latitude: float, longitude: float, now: datetime) -> list[Warning]:
    """Return warnings covering the location that have not ended, most severe first."""
    found = [
        w for w in warnings if (w.end is None or w.end > now) and covers(w, latitude, longitude)
    ]
    return sorted(found, key=lambda w: (-LEVELS.index(w.level), w.start or now))


def highest_level(warnings: list[Warning]) -> str:
    """Return the most severe level, or none."""
    return max((w.level for w in warnings), key=LEVELS.index, default="none")
