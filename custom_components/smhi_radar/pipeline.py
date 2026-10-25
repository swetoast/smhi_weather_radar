"""Executor jobs: decode frames, analyse, render."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

import numpy as np
from PIL import Image

from .analysis import Grid, PointReading, decode_frame, nearby_rain, rain_rate_grid, read_point
from .coverage import coverage_grid
from .models import Analysis
from .nowcast import nowcast
from .render import Marker, local_view, national_view, render_view


@dataclass(frozen=True, slots=True)
class StoredFrame:
    """One frame kept in the history: the original PNG and its dBZ grid."""

    valid: datetime
    png: bytes
    dbz: np.ndarray


@dataclass(frozen=True, slots=True)
class Settings:
    """Per entry settings that affect analysis and rendering."""

    latitude: float
    longitude: float
    nearby_km: float
    local_km: float
    animate: bool


def decode(valid: datetime, png: bytes) -> StoredFrame:
    """Decode a downloaded frame for the history."""
    _, dbz = decode_frame(png)
    return StoredFrame(valid, png, dbz)


def analyse(frames: list[StoredFrame], settings: Settings, online: frozenset[str] | None) -> Analysis:
    """Analyse the newest frame and the history (oldest first).

    online lists the radars delivering data (None when unknown: everything
    counts as measured). Pixels no working radar measures are unknown, or
    filled from older frames along the motion where possible.
    """
    current = frames[-1]
    height, width = current.dbz.shape
    grid = Grid(width, height)
    lat, lon = settings.latitude, settings.longitude
    home_x, home_y = grid.to_pixel(lat, lon)
    same_shape = {f.valid: f.dbz for f in frames if f.dbz.shape == current.dbz.shape}
    covered = None if online is None else coverage_grid(width, height, online)
    result = nowcast(same_shape, home_x, home_y, grid.pixel_width / 1000, covered)

    rate = rain_rate_grid(current.dbz)
    filled = np.zeros(rate.shape, bool)
    if covered is not None:
        rate = np.where(covered, rate, np.nan).astype(np.float32)
    if result.local is not None:
        _paste(rate, result.local.values, result.local.x0, result.local.y0)
        _paste(filled, result.local.filled, result.local.x0, result.local.y0)

    reading = read_point(current.dbz, grid, lat, lon)
    x, y = reading.pixel_x, reading.pixel_y
    own_covered = covered is None or bool(covered[y, x])
    intensity = None if np.isnan(rate[y, x]) else float(rate[y, x])
    if not own_covered:
        reading = PointReading(None, intensity, x, y)
    nearby = nearby_rain(rate, grid, lat, lon, settings.nearby_km, settings.local_km)
    return Analysis(
        reading=reading,
        nearby=nearby,
        nowcast=result,
        reading_source=_source(intensity, bool(filled[y, x])),
        nearby_source=_source(nearby.raining, _disc_filled(filled, grid, home_x, home_y, settings.nearby_km)),
        covered=own_covered,
    )


def _source(value, filled: bool) -> str | None:
    if value is None:
        return None
    return "extrapolated" if filled else "measured"


def _disc_filled(filled: np.ndarray, grid: Grid, home_x: float, home_y: float, radius_km: float) -> bool:
    reach = radius_km * 1000 / grid.pixel_width
    x0, x1 = max(0, int(home_x - reach)), min(filled.shape[1], int(home_x + reach) + 2)
    y0, y1 = max(0, int(home_y - reach)), min(filled.shape[0], int(home_y + reach) + 2)
    return bool(filled[y0:y1, x0:x1].any())


def _paste(target: np.ndarray, window: np.ndarray, x0: int, y0: int) -> None:
    """Copy a nowcast window back into the full grid (clipped at the edges)."""
    height, width = target.shape
    size_y, size_x = window.shape
    tx0, ty0 = max(0, x0), max(0, y0)
    tx1, ty1 = min(width, x0 + size_x), min(height, y0 + size_y)
    if tx1 > tx0 and ty1 > ty0:
        target[ty0:ty1, tx0:tx1] = window[ty0 - y0 : ty1 - y0, tx0 - x0 : tx1 - x0]


def render(
    frames: list[StoredFrame],
    settings: Settings,
    markers: list[Marker],
    stamps: dict[datetime, str],
) -> tuple[bytes, bytes]:
    """Render the national and local images (animated when enabled)."""
    selected = frames if settings.animate else frames[-1:]
    decoded = []
    for frame in selected:
        with Image.open(BytesIO(frame.png)) as source:
            decoded.append((source.convert("RGBA"), stamps[frame.valid]))
    width, height = decoded[-1][0].size
    # Animation only uses frames with the current size.
    decoded = [item for item in decoded if item[0].size == (width, height)]
    national = render_view(decoded, national_view(width, height), markers)
    local = render_view(
        decoded,
        local_view(settings.latitude, settings.longitude, settings.local_km, settings.nearby_km),
        markers,
    )
    return national, local
