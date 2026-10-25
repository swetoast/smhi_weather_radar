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
import math
from typing import Any, Final

from .exceptions import SmhiRadarDataError
from .projection import to_sweref99tm

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
    """Whether the location is measured right now."""

    covered: bool
    radars: tuple[RadarSite, ...]  # nearest first, within reach

    @property
    def offline(self) -> list[str]:
        """Return names of nearby radars that are not delivering."""
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
    nearest = radars[0]
    covered = nearest.online or any(r.online and r.distance_km <= FILL_IN_KM for r in radars)
    reach = max(FILL_IN_KM, nearest.distance_km)
    return Coverage(covered, tuple(r for r in radars if r.distance_km <= reach))
