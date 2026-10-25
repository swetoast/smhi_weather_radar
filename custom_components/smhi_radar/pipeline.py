"""Executor jobs: decode frames, analyse, render."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

import numpy as np
from PIL import Image

from .analysis import Grid, decode_frame, nearby_rain, rain_rate_grid, read_point
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


def analyse(frames: list[StoredFrame], settings: Settings) -> Analysis:
    """Analyse the newest frame and the history (oldest first)."""
    current = frames[-1]
    grid = Grid(current.dbz.shape[1], current.dbz.shape[0])
    lat, lon = settings.latitude, settings.longitude
    home_x, home_y = grid.to_pixel(lat, lon)
    same_shape = {f.valid: f.dbz for f in frames if f.dbz.shape == current.dbz.shape}
    return Analysis(
        reading=read_point(current.dbz, grid, lat, lon),
        nearby=nearby_rain(
            rain_rate_grid(current.dbz), grid, lat, lon, settings.nearby_km, settings.local_km
        ),
        nowcast=nowcast(same_shape, home_x, home_y, grid.pixel_width / 1000),
    )


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
