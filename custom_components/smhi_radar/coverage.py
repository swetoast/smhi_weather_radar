"""Which SMHI radars cover the location, and are they delivering data.

The composite PNG is transparent both where a radar sees no precipitation
and where no radar is measuring at all, so an offline radar looks like
fine weather. The radar API lists every radar with the time it last
delivered data; that tells the two apart.

Measured on the composite (October 2026, Vara offline): a neighbouring
radar fills in to about 140 km. A location is covered when its nearest
radar is delivering, or when another delivering radar is within that
distance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
import math
from typing import Any, Final

import numpy as np

from .const import SWEREF_MAX_EASTING, SWEREF_MAX_NORTHING, SWEREF_MIN_EASTING, SWEREF_MIN_NORTHING
from .exceptions import SmhiRadarDataError
from .projection import to_sweref99tm

STATES: Final = ("full", "filled", "gap")
FILL_IN_KM: Final = 140.0
OFFLINE_AFTER: Final = timedelta(minutes=30)

# Radar sites (latitude, longitude) from the WMO/OPERA radar databases,
# keyed like the SMHI radar API areas.
SITES: Final[dict[str, tuple[str, float, float]]] = {
    "angelholm": ("\u00c4ngelholm", 56.3675, 12.8517),
    "atvidaberg": ("\u00c5tvidaberg", 58.1059, 15.9363),
    "balsta": ("B\u00e5lsta", 59.6519, 17.9178),
    "hemse": ("Hemse", 57.3035, 18.4001),
    "hudiksvall": ("Hudiksvall", 61.5771, 16.7144),
    "karlskrona": ("Karlskrona", 56.2955, 15.6102),
    "kiruna": ("Kiruna", 67.7088, 20.6178),
    "leksand": ("Leksand", 60.7230, 14.8776),
    "lulea": ("Lule\u00e5", 65.4309, 21.8650),
    "ornskoldsvik": ("\u00d6rnsk\u00f6ldsvik", 63.6395, 18.4024),
    "ostersund": ("\u00d6stersund", 63.2950, 14.7591),
    "vara": ("Vara", 58.2556, 12.8260),
}


@dataclass(frozen=True, slots=True)
class RadarSite:
    """One radar as seen from the location."""

    key: str
    name: str
    distance_km: float
    updated: datetime | None
    online: bool


@dataclass(frozen=True, slots=True)
class Coverage:
    """Whether the location is measured right now.

    state: "full" (the nearest radar delivers), "filled" (it does not, but a
    neighbour reaches the location) or "gap" (no working radar reaches it).
    """

    state: str
    radars: tuple[RadarSite, ...]  # every radar, nearest first

    @property
    def covered(self) -> bool:
        """Return True when a working radar measures the location."""
        return self.state != "gap"

    @property
    def offline(self) -> list[str]:
        """Return names of all radars that are not delivering."""
        return [radar.name for radar in self.radars if not radar.online]


def parse_status(payload: Any) -> dict[str, datetime]:
    """Return radar key -> last delivery from /api/version/latest.json."""
    if not isinstance(payload, dict) or not isinstance(payload.get("areas"), list):
        raise SmhiRadarDataError("SMHI radar status has no areas")
    result = {}
    for area in payload["areas"]:
        if not isinstance(area, dict) or not isinstance(area.get("updated"), str):
            continue
        try:
            updated = datetime.fromisoformat(area["updated"].replace("Z", "+00:00"))
        except ValueError:
            continue
        result[str(area.get("key"))] = updated if updated.tzinfo else updated.replace(tzinfo=UTC)
    return result


def coverage(status: dict[str, datetime], latitude: float, longitude: float, now: datetime) -> Coverage:
    """Return coverage of the location given each radar's last delivery."""
    easting, northing = to_sweref99tm(latitude, longitude)
    radars = []
    for key, (name, lat, lon) in SITES.items():
        site_e, site_n = to_sweref99tm(lat, lon)
        updated = status.get(key)
        radars.append(
            RadarSite(
                key=key,
                name=name,
                distance_km=round(math.hypot(easting - site_e, northing - site_n) / 1000, 1),
                updated=updated,
                online=updated is not None and now - updated <= OFFLINE_AFTER,
            )
        )
    radars.sort(key=lambda radar: radar.distance_km)
    if radars[0].online:
        state = "full"
    elif any(r.online and r.distance_km <= FILL_IN_KM for r in radars):
        state = "filled"
    else:
        state = "gap"
    return Coverage(state, tuple(radars))


def online_radars(status: dict[str, datetime], now: datetime) -> frozenset[str]:
    """Return the keys of radars that delivered data recently."""
    return frozenset(key for key, updated in status.items() if key in SITES and now - updated <= OFFLINE_AFTER)


@lru_cache(maxsize=8)
def coverage_grid(width: int, height: int, online: frozenset[str]) -> np.ndarray:
    """Return a bool grid of composite pixels a working radar measures.

    Same rule as for the location: the nearest radar delivers, or another
    delivering radar is within FILL_IN_KM.
    """
    pixel_e = (SWEREF_MAX_EASTING - SWEREF_MIN_EASTING) / width
    pixel_n = (SWEREF_MAX_NORTHING - SWEREF_MIN_NORTHING) / height
    easting = SWEREF_MIN_EASTING + (np.arange(width, dtype=np.float32) + 0.5) * pixel_e
    northing = SWEREF_MAX_NORTHING - (np.arange(height, dtype=np.float32) + 0.5) * pixel_n
    nearest = np.full((height, width), np.inf, np.float32)
    nearest_online = np.zeros((height, width), bool)
    filled = np.zeros((height, width), bool)
    for key, (_, lat, lon) in SITES.items():
        site_e, site_n = to_sweref99tm(lat, lon)
        distance = np.hypot(easting[None, :] - site_e, northing[:, None] - site_n) / 1000
        closer = distance < nearest
        nearest = np.where(closer, distance, nearest)
        nearest_online = np.where(closer, key in online, nearest_online)
        if key in online:
            filled |= distance <= FILL_IN_KM
    grid = nearest_online | filled
    grid.flags.writeable = False
    return grid
