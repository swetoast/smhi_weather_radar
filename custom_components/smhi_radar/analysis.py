"""Radar frame decoding and precipitation analysis.

Frames are decoded to a dBZ grid (int8) that is cheap to keep in memory for
the last hour. Everything here is blocking and runs in the executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from typing import Final

import numpy as np
from PIL import Image, UnidentifiedImageError

from .exceptions import SmhiRadarDataError
from .const import (
    SWEREF_MAX_EASTING,
    SWEREF_MAX_NORTHING,
    SWEREF_MIN_EASTING,
    SWEREF_MIN_NORTHING,
)
from .projection import to_sweref99tm

# SMHI PNG colour scale (https://opendata.smhi.se/apidocs/radar/data.html).
_GREYS: Final = [*range(54, 77), 87, 97, 108, 118, 129, 139, 150, 160, 171, 181, 192]
_COLOURS: Final = [
    (0, 50, 255), (0, 70, 255), (0, 90, 255), (0, 110, 255), (0, 130, 255),
    (0, 150, 255), (0, 170, 255), (0, 128, 0), (0, 138, 0), (0, 148, 0),
    (0, 158, 0), (0, 163, 0), (0, 168, 0), (0, 173, 0), (0, 178, 0),
    (10, 208, 10), (10, 218, 10), (10, 228, 10), (10, 238, 10), (10, 248, 10),
    (255, 255, 15), (255, 246, 15), (255, 238, 15), (255, 229, 15), (255, 220, 15),
    (255, 200, 0), (255, 180, 0), (255, 160, 0), (255, 140, 0), (255, 120, 0),
    (255, 35, 35), (255, 15, 15), (255, 0, 0), (235, 0, 0), (215, 0, 0),
    (195, 0, 0), (175, 0, 0), (155, 0, 0), (135, 0, 0), (115, 0, 0),
    (175, 0, 175), (184, 0, 184), (193, 0, 193), (202, 0, 202), (211, 0, 211),
    (219, 0, 219), (228, 0, 228), (237, 0, 237), (246, 0, 246), (255, 0, 255),
    (0, 255, 255), (13, 255, 255), (26, 255, 255), (39, 255, 255), (51, 255, 255),
    (64, 255, 255), (77, 255, 255), (90, 255, 255), (102, 255, 255), (115, 255, 255),
    (128, 255, 255), (141, 255, 255), (154, 255, 255), (166, 255, 255), (179, 255, 255),
    (192, 255, 255),
]  # fmt: skip
RGB_TO_DBZ: Final[dict[tuple[int, int, int], int]] = {
    **{(grey, grey, grey): dbz for dbz, grey in enumerate(_GREYS, start=-29)},
    **{rgb: dbz for dbz, rgb in enumerate(_COLOURS, start=5)},
}

# int8 codes in the dBZ grid besides real values (-29..70).
NO_ECHO: Final = -64  # transparent pixel: covered, nothing detected
NO_DATA: Final = -128  # any other colour: outside coverage, logo, unknown
RAIN_MIN_DBZ: Final = 5  # SMHI: below 5 dBZ is not counted as precipitation

_KEYS = np.array(
    sorted((r << 16) | (g << 8) | b for (r, g, b) in RGB_TO_DBZ), dtype=np.int32
)
_VALUES = np.array(
    [RGB_TO_DBZ[((k >> 16) & 255, (k >> 8) & 255, k & 255)] for k in _KEYS.tolist()],
    dtype=np.int8,
)

# 16 point compass, clockwise from north.
COMPASS: Final = (
    "n", "nne", "ne", "ene", "e", "ese", "se", "sse",
    "s", "ssw", "sw", "wsw", "w", "wnw", "nw", "nnw",
)  # fmt: skip


def rain_rate_from_dbz(dbz: float) -> float:
    """Return mm/h from dBZ using SMHI's Z = 200 * R^1.5."""
    if dbz < RAIN_MIN_DBZ:
        return 0.0
    return (10 ** (dbz / 10) / 200) ** (1 / 1.5)


def rain_rate_grid(dbz: np.ndarray) -> np.ndarray:
    """Return a float32 mm/h grid; NaN where there is no data."""
    values = dbz.astype(np.float32)
    rate = np.where(
        values >= RAIN_MIN_DBZ,
        (np.power(10.0, values / 10) / 200) ** (1 / 1.5),
        0.0,
    ).astype(np.float32)
    rate[dbz == NO_DATA] = np.nan
    return rate


def in_coverage(latitude: float, longitude: float) -> bool:
    """Return True if the coordinate is inside the SMHI radar composite."""
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return False
    easting, northing = to_sweref99tm(latitude, longitude)
    return (
        SWEREF_MIN_EASTING <= easting <= SWEREF_MAX_EASTING
        and SWEREF_MIN_NORTHING <= northing <= SWEREF_MAX_NORTHING
    )


def compass_point(bearing: float) -> str:
    """Return the 16 point compass key for a bearing in degrees."""
    return COMPASS[round((bearing % 360) / 22.5) % 16]


@dataclass(frozen=True, slots=True)
class Grid:
    """Geometry of a radar frame in SWEREF99 TM."""

    width: int
    height: int

    @property
    def pixel_width(self) -> float:
        """Return metres per pixel along easting."""
        return (SWEREF_MAX_EASTING - SWEREF_MIN_EASTING) / self.width

    @property
    def pixel_height(self) -> float:
        """Return metres per pixel along northing."""
        return (SWEREF_MAX_NORTHING - SWEREF_MIN_NORTHING) / self.height

    def to_pixel(self, latitude: float, longitude: float) -> tuple[float, float]:
        """Return fractional pixel coordinates (x right, y down)."""
        easting, northing = to_sweref99tm(latitude, longitude)
        return (
            (easting - SWEREF_MIN_EASTING) / self.pixel_width,
            (SWEREF_MAX_NORTHING - northing) / self.pixel_height,
        )

    def containing_pixel(self, latitude: float, longitude: float) -> tuple[int, int]:
        """Return the pixel that contains a coordinate (clamped to the frame)."""
        x, y = self.to_pixel(latitude, longitude)
        return (
            min(self.width - 1, max(0, math.floor(x))),
            min(self.height - 1, max(0, math.floor(y))),
        )


def decode_frame(data: bytes) -> tuple[Image.Image, np.ndarray]:
    """Decode an SMHI PNG into an RGBA image and an int8 dBZ grid."""
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format != "PNG":
                raise SmhiRadarDataError("SMHI radar image is not a PNG")
            image = source.convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as err:
        raise SmhiRadarDataError("SMHI returned an invalid radar image") from err

    rgba = np.asarray(image)
    packed = (
        (rgba[..., 0].astype(np.int32) << 16)
        | (rgba[..., 1].astype(np.int32) << 8)
        | rgba[..., 2].astype(np.int32)
    )
    index = np.clip(np.searchsorted(_KEYS, packed), 0, len(_KEYS) - 1)
    known = _KEYS[index] == packed
    dbz = np.full(packed.shape, NO_DATA, dtype=np.int8)
    dbz[known] = _VALUES[index[known]]
    dbz[rgba[..., 3] == 0] = NO_ECHO
    return image, dbz


@dataclass(frozen=True, slots=True)
class PointReading:
    """Radar reading at the configured location."""

    reflectivity: int | None
    intensity: float | None
    pixel_x: int
    pixel_y: int


@dataclass(frozen=True, slots=True)
class NearbyRain:
    """Precipitation around the configured location."""

    raining: bool | None
    max_intensity: float | None
    nearest_km: float | None
    nearest_bearing: float | None
    nearest_direction: str | None


def read_point(dbz: np.ndarray, grid: Grid, latitude: float, longitude: float) -> PointReading:
    """Return reflectivity and intensity at a location."""
    x, y = grid.containing_pixel(latitude, longitude)
    value = int(dbz[y, x])
    if value == NO_ECHO:
        return PointReading(None, 0.0, x, y)
    if value == NO_DATA:
        return PointReading(None, None, x, y)
    return PointReading(value, rain_rate_from_dbz(value), x, y)


def nearby_rain(
    rate: np.ndarray,
    grid: Grid,
    latitude: float,
    longitude: float,
    radius_km: float,
    search_km: float,
) -> NearbyRain:
    """Return rain within radius_km and the nearest rain within search_km."""
    home_x, home_y = grid.to_pixel(latitude, longitude)
    reach = max(radius_km, search_km) * 1000
    x0 = max(0, math.floor(home_x - reach / grid.pixel_width))
    x1 = min(grid.width, math.ceil(home_x + reach / grid.pixel_width) + 1)
    y0 = max(0, math.floor(home_y - reach / grid.pixel_height))
    y1 = min(grid.height, math.ceil(home_y + reach / grid.pixel_height) + 1)
    window = rate[y0:y1, x0:x1]

    # Distances from the location to each pixel centre, in metres.
    east = (np.arange(x0, x1) + 0.5 - home_x) * grid.pixel_width
    south = (np.arange(y0, y1) + 0.5 - home_y) * grid.pixel_height
    distance = np.hypot(east[np.newaxis, :], south[:, np.newaxis])

    inside = distance <= radius_km * 1000
    values = window[inside]
    unknown = np.isnan(values)
    if values.size == 0 or unknown.all():
        raining, max_intensity = None, None
    else:
        max_intensity = float(np.nanmax(values))
        raining = max_intensity > 0
        if not raining and unknown.any():
            # Dry where measured, but part of the radius is not measured.
            raining, max_intensity = None, None

    searched = distance <= search_km * 1000
    rain = (window > 0) & searched
    gap = np.isnan(window) & searched
    if not rain.any():
        return NearbyRain(raining, max_intensity, None, None, None)
    masked = np.where(rain, distance, np.inf)
    if gap.any() and float(distance[gap].min()) < float(masked.min()):
        # Unmeasured ground is closer than the nearest measured rain.
        return NearbyRain(raining, max_intensity, None, None, None)
    row, col = np.unravel_index(int(np.argmin(masked)), masked.shape)
    # Rain in the location's own pixel counts as zero distance.
    own_pixel = (row + y0, col + x0) == (math.floor(home_y), math.floor(home_x))
    nearest = 0.0 if own_pixel else float(masked[row, col])
    bearing = math.degrees(math.atan2(east[col], -south[row])) % 360
    return NearbyRain(
        raining,
        max_intensity,
        round(nearest / 1000, 1),
        round(bearing),
        compass_point(bearing),
    )
