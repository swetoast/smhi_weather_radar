"""Radar map rendering (blocking, run in the executor).

Two views are drawn from the same frames: all of Sweden at twice the radar
resolution, and a square local view around the configured location. Radar
pixels are scaled with nearest neighbour so the data is shown exactly as
SMHI published it; only the basemap and overlays are smoothed.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache, lru_cache
from io import BytesIO
import json
import math
from pathlib import Path
from typing import Final

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .analysis import RGB_TO_DBZ
from .const import (
    SWEREF_MAX_EASTING,
    SWEREF_MAX_NORTHING,
    SWEREF_MIN_EASTING,
    SWEREF_MIN_NORTHING,
)
from .projection import to_sweref99tm

_ASSETS: Final = Path(__file__).parent / "assets"

# Approved palette.
_SEA: Final = (28, 43, 55, 255)
_NEIGHBOUR_LAND: Final = (52, 64, 74, 255)
_SWEDEN_LAND: Final = (92, 102, 112, 255)
_COUNTY_LINE: Final = (120, 130, 140, 255)
_COAST_LINE: Final = (150, 160, 170, 255)
HOME_COLOUR: Final = (3, 169, 244, 255)
PERSON_COLOUR: Final = (255, 152, 0, 255)
_WHITE: Final = (255, 255, 255, 255)

_HOME_GLYPH: Final = "\U000f02dc"  # mdi:home
_PERSON_GLYPH: Final = "\U000f0004"  # mdi:account

NATIONAL_SCALE: Final = 2
LOCAL_SIZE: Final = 720
_FRAME_MS: Final = 400
_LAST_FRAME_MS: Final = 1600

FULL_EXTENT: Final = (
    SWEREF_MIN_EASTING,
    SWEREF_MIN_NORTHING,
    SWEREF_MAX_EASTING,
    SWEREF_MAX_NORTHING,
)

type Extent = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Marker:
    """A marker to draw; kind is "home" or "person"."""

    kind: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class View:
    """What to draw: an extent in SWEREF99 TM and an output size."""

    extent: Extent
    size: tuple[int, int]
    marker_size: int
    text_scale: float
    ring_km: float | None = None


def national_view(frame_width: int, frame_height: int) -> View:
    """Return the all of Sweden view."""
    return View(
        FULL_EXTENT,
        (frame_width * NATIONAL_SCALE, frame_height * NATIONAL_SCALE),
        marker_size=30,
        text_scale=2.0,
    )


def local_view(latitude: float, longitude: float, radius_km: float, ring_km: float) -> View:
    """Return a square view centred on the location."""
    easting, northing = to_sweref99tm(latitude, longitude)
    reach = radius_km * 1000
    return View(
        (easting - reach, northing - reach, easting + reach, northing + reach),
        (LOCAL_SIZE, LOCAL_SIZE),
        marker_size=34,
        text_scale=1.4,
        ring_km=ring_km,
    )


def _to_xy(easting: float, northing: float, extent: Extent, size: tuple[int, int]) -> tuple[float, float]:
    e0, n0, e1, n1 = extent
    return (easting - e0) / (e1 - e0) * size[0], (n1 - northing) / (n1 - n0) * size[1]


@cache
def _load_basemap() -> dict[str, list]:
    return json.loads((_ASSETS / "basemap.json").read_text(encoding="utf-8"))["layers"]


@lru_cache(maxsize=4)
def _basemap(extent: Extent, size: tuple[int, int]) -> Image.Image:
    """Render the basemap for a view (supersampled, cached)."""
    layers = _load_basemap()
    scale = 2
    big = (size[0] * scale, size[1] * scale)
    canvas = Image.new("RGBA", big, _SEA)
    draw = ImageDraw.Draw(canvas)
    county_width, coast_width = 2, 3  # in supersampled pixels, as approved

    def ring_xy(ring: list[list[int]]) -> list[tuple[float, float]]:
        return [_to_xy(e, n, extent, big) for e, n in ring]

    def fill(polygons: list, colour: tuple[int, int, int, int]) -> None:
        for polygon in polygons:
            draw.polygon(ring_xy(polygon[0]), fill=colour)
            for hole in polygon[1:]:
                draw.polygon(ring_xy(hole), fill=_SEA)

    def outline(polygons: list, colour: tuple[int, int, int, int], width: int) -> None:
        for polygon in polygons:
            for ring in polygon:
                draw.line(ring_xy(ring), fill=colour, width=width)

    fill(layers["neighbours"], _NEIGHBOUR_LAND)
    fill(layers["sweden"], _SWEDEN_LAND)
    outline(layers["counties"], _COUNTY_LINE, county_width)
    fill(layers["lakes"], _SEA)
    outline(layers["sweden"], _COAST_LINE, coast_width)
    return canvas.resize(size, Image.Resampling.LANCZOS)


@cache
def _font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont | None:
    """Load a bundled font; None if FreeType is unavailable."""
    try:
        return ImageFont.truetype(str(_ASSETS / name), size)
    except OSError:
        return None


def _radar_layer(frame: Image.Image, view: View) -> Image.Image:
    """Cut and scale the radar frame to the view (nearest neighbour)."""
    width, height = frame.size
    e0, n0, e1, n1 = view.extent
    fe0, fn0, fe1, fn1 = FULL_EXTENT
    box = (
        (e0 - fe0) / (fe1 - fe0) * width,
        (fn1 - n1) / (fn1 - fn0) * height,
        (e1 - fe0) / (fe1 - fe0) * width,
        (fn1 - n0) / (fn1 - fn0) * height,
    )
    return frame.transform(
        view.size, Image.Transform.EXTENT, box, Image.Resampling.NEAREST, fillcolor=(0, 0, 0, 0)
    )


def _badge(draw: ImageDraw.ImageDraw, x: float, y: float, glyph: str, colour, size: int) -> None:
    radius = size / 2
    draw.ellipse((x - radius - 3, y - radius - 3, x + radius + 3, y + radius + 3), fill=(0, 0, 0, 110))
    draw.ellipse(
        (x - radius, y - radius, x + radius, y + radius),
        fill=colour,
        outline=_WHITE,
        width=max(2, size // 14),
    )
    font = _font("mdi_icons.ttf", round(size * 0.62))
    if font is not None:
        draw.text((x, y), glyph, font=font, fill=_WHITE, anchor="mm")
    else:
        dot = size / 6
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=_WHITE)


def _markers(draw: ImageDraw.ImageDraw, view: View, markers: list[Marker]) -> None:
    """Draw people first and home on top; people at home sit beside it."""
    size = view.marker_size
    placed: list[tuple[float, float]] = []
    home_xy: tuple[float, float] | None = None
    points = []
    for marker in markers:
        x, y = _to_xy(*to_sweref99tm(marker.latitude, marker.longitude), view.extent, view.size)
        if -size <= x <= view.size[0] + size and -size <= y <= view.size[1] + size:
            points.append((marker, x, y))
            if marker.kind == "home":
                home_xy = (x, y)

    offsets = [(0.95, -0.35), (-0.95, -0.35), (0.0, -1.05), (0.95, 0.6), (-0.95, 0.6)]
    for marker, x, y in points:
        if marker.kind != "person":
            continue
        crowded = [p for p in [home_xy, *placed] if p and math.dist(p, (x, y)) < size * 0.9]
        if crowded and home_xy is not None:
            dx, dy = offsets[len(placed) % len(offsets)]
            x, y = home_xy[0] + dx * size, home_xy[1] + dy * size
        placed.append((x, y))
        _badge(draw, x, y, _PERSON_GLYPH, PERSON_COLOUR, size)
    if home_xy is not None:
        _badge(draw, *home_xy, _HOME_GLYPH, HOME_COLOUR, size)


def _ring(draw: ImageDraw.ImageDraw, view: View, centre: Marker) -> None:
    """Draw the dashed nearby radius."""
    x, y = _to_xy(*to_sweref99tm(centre.latitude, centre.longitude), view.extent, view.size)
    radius = view.ring_km * 1000 / (view.extent[2] - view.extent[0]) * view.size[0]
    for start in range(0, 360, 8):
        draw.arc((x - radius, y - radius, x + radius, y + radius), start, start + 4, fill=(255, 255, 255, 170), width=2)


def _timestamp(draw: ImageDraw.ImageDraw, view: View, text: str) -> None:
    font = _font("roboto_digits.ttf", round(13 * view.text_scale))
    if font is None:
        font = ImageFont.load_default()
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    pad = round(6 * view.text_scale)
    x = view.size[0] - (right - left) - pad * 3
    y = view.size[1] - (bottom - top) - pad * 3
    draw.rounded_rectangle(
        (x - pad, y - pad + top, x + (right - left) + pad, y + bottom + pad),
        radius=round(8 * view.text_scale),
        fill=(0, 0, 0, 150),
    )
    draw.text((x, y), text, font=font, fill=_WHITE)


_RADAR_RGB: Final = np.array(sorted(RGB_TO_DBZ), dtype=np.uint8)
_RADAR_KEYS: Final = (
    (_RADAR_RGB[:, 0].astype(np.int32) << 16)
    | (_RADAR_RGB[:, 1].astype(np.int32) << 8)
    | _RADAR_RGB[:, 2].astype(np.int32)
)
_OTHER_COLOURS: Final = 256 - len(_RADAR_RGB)


def _to_shared_palette(images: list[Image.Image]) -> list[Image.Image]:
    """Convert RGB frames to one shared palette for a compact animation.

    All SMHI radar colours get their own exact palette entries, so the data
    is never altered; the basemap and overlays share the remaining entries.
    """
    arrays = [np.asarray(image) for image in images]
    masks = []
    for array in arrays:
        packed = (
            (array[..., 0].astype(np.int32) << 16)
            | (array[..., 1].astype(np.int32) << 8)
            | array[..., 2].astype(np.int32)
        )
        index = np.clip(np.searchsorted(_RADAR_KEYS, packed), 0, len(_RADAR_KEYS) - 1)
        masks.append((_RADAR_KEYS[index] == packed, index))

    # Palette for everything that is not radar data, from the newest frame.
    background = arrays[-1].copy()
    background[masks[-1][0]] = _SEA[:3]
    reference = Image.fromarray(background).quantize(
        colors=_OTHER_COLOURS, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE
    )
    palette = reference.getpalette()[: _OTHER_COLOURS * 3]
    palette += [0] * (_OTHER_COLOURS * 3 - len(palette))
    palette_image = Image.new("P", (1, 1))
    palette_image.putpalette(palette + _RADAR_RGB.flatten().tolist())

    result = []
    for array, (is_radar, radar_index) in zip(arrays, masks, strict=True):
        background = array.copy()
        background[is_radar] = _SEA[:3]
        mapped = Image.fromarray(background).quantize(
            palette=palette_image, dither=Image.Dither.NONE
        )
        indices = np.asarray(mapped).copy()
        indices[is_radar] = _OTHER_COLOURS + radar_index[is_radar]
        frame = Image.fromarray(indices.astype(np.uint8), mode="P")
        frame.putpalette(palette_image.getpalette())
        result.append(frame)
    return result


def render_view(
    frames: list[tuple[Image.Image, str]],
    view: View,
    markers: list[Marker],
) -> bytes:
    """Render frames (oldest first, with timestamp text) to PNG or animated PNG."""
    base = _basemap(view.extent, view.size)
    home = next((m for m in markers if m.kind == "home"), None)
    images = []
    for frame, stamp in frames:
        canvas = base.copy()
        canvas.alpha_composite(_radar_layer(frame, view))
        draw = ImageDraw.Draw(canvas)
        if view.ring_km and home is not None:
            _ring(draw, view, home)
        _markers(draw, view, markers)
        _timestamp(draw, view, stamp)
        images.append(canvas.convert("RGB"))

    output = BytesIO()
    if len(images) == 1:
        images[0].save(output, format="PNG", compress_level=6)
    else:
        images = _to_shared_palette(images)
        images[0].save(
            output,
            format="PNG",
            save_all=True,
            append_images=images[1:],
            duration=[_FRAME_MS] * (len(images) - 1) + [_LAST_FRAME_MS],
            loop=0,
            compress_level=6,
        )
    return output.getvalue()
