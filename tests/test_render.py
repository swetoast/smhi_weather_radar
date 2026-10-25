"""Tests for the renderer."""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

from custom_components.smhi_radar import render as R
from custom_components.smhi_radar.analysis import RGB_TO_DBZ

from .conftest import HEAVY, HEIGHT, HOME, LIGHT, WIDTH, cell_frame, frame_times

MARKERS = [R.Marker("person", 57.79, 13.42), R.Marker("home", *HOME)]


def _frames(count: int) -> list[tuple[Image.Image, str]]:
    return [
        (Image.open(BytesIO(cell_frame(valid))).convert("RGBA"), f"2026-10-24 17:{i:02d}")
        for i, valid in enumerate(frame_times()[-count:])
    ]


def test_static_views() -> None:
    """Sizes, markers and radar colours in both views."""
    national = Image.open(BytesIO(R.render_view(_frames(1), R.national_view(WIDTH, HEIGHT), MARKERS)))
    assert national.size == (WIDTH * 2, HEIGHT * 2)

    view = R.local_view(*HOME, 100, 10)
    local = np.asarray(Image.open(BytesIO(R.render_view(_frames(1), view, MARKERS))).convert("RGB"))
    assert local.shape == (720, 720, 3)
    # Home badge colour at the centre, radar colours present unchanged.
    assert tuple(local[360, 360 - 12]) == R.HOME_COLOUR[:3]
    colours = {tuple(c) for c in local.reshape(-1, 3)}
    assert LIGHT in colours and HEAVY in colours


def test_animation_keeps_radar_colours_exact() -> None:
    """Animated output uses a shared palette without altering radar data."""
    frames = _frames(13)
    view = R.local_view(*HOME, 100, 10)
    animated = Image.open(BytesIO(R.render_view(frames, view, MARKERS)))
    assert animated.n_frames == 13
    for index in (0, 6, 12):
        animated.seek(index)
        got = np.asarray(animated.convert("RGB"))
        expected = np.asarray(
            Image.open(BytesIO(R.render_view([frames[index]], view, MARKERS))).convert("RGB")
        )
        radar = np.isin(
            (expected[..., 0].astype(np.int32) << 16)
            | (expected[..., 1].astype(np.int32) << 8)
            | expected[..., 2],
            [(r << 16) | (g << 8) | b for r, g, b in RGB_TO_DBZ],
        )
        assert radar.any()
        assert (got[radar] == expected[radar]).all()


def test_renders_without_fonts(monkeypatch) -> None:
    """Missing FreeType support still gives an image."""
    monkeypatch.setattr(R, "_font", lambda name, size: None)
    image = Image.open(BytesIO(R.render_view(_frames(1), R.local_view(*HOME, 50, 10), MARKERS)))
    assert image.size == (720, 720)
