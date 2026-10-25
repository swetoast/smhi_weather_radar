"""Battle tests for the nowcast on simulated rain."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
import random

import numpy as np

from custom_components.smhi_radar.analysis import NO_ECHO, decode_frame
from custom_components.smhi_radar.nowcast import nowcast

from .conftest import GRID, HOME, cell_frame, frame_times

PIXEL_KM = GRID.pixel_width / 1000
T0 = datetime(2026, 10, 24, 14, tzinfo=UTC)


def _to_dbz(rate: np.ndarray) -> np.ndarray:
    dbz = np.full(rate.shape, NO_ECHO, dtype=np.int8)
    wet = rate > 0
    dbz[wet] = np.clip(np.round(10 * np.log10(200 * rate[wet] ** 1.5)), 5, 70)
    return dbz


def _world(seed: int, speed_kmh: float, heading: float, clutter: float, speckle: float):
    """Return an hour of dBZ frames with random cells moving at a known velocity."""
    rng = np.random.default_rng(seed)
    pick = random.Random(seed)
    home_x, home_y = GRID.to_pixel(*HOME)
    v = speed_kmh / PIXEL_KM / 60
    vx, vy = v * math.sin(math.radians(heading)), -v * math.cos(math.radians(heading))
    cells = [
        (
            home_x + pick.uniform(-70, 70),
            home_y + pick.uniform(-70, 70),
            pick.uniform(3, 12),
            pick.uniform(1, 15),
        )
        for _ in range(pick.randint(6, 25))
    ]
    static = rng.random((GRID.height, GRID.width)) < clutter
    yy, xx = np.mgrid[0 : GRID.height, 0 : GRID.width].astype(np.float32)
    frames = {}
    for step in range(13):
        minutes = 5 * step
        rate = np.zeros((GRID.height, GRID.width), np.float32)
        for x, y, r, a in cells:
            rate += a * np.exp(-((xx - x - vx * minutes) ** 2 + (yy - y - vy * minutes) ** 2) / (2 * r * r))
        rate[rate < 0.2] = 0
        rate[static] = np.maximum(rate[static], 3.0)
        noise = rng.random(rate.shape) < speckle
        rate[noise] = np.maximum(rate[noise], 0.5)
        frames[T0 + timedelta(minutes=minutes)] = _to_dbz(rate)
    return frames


def test_motion_battery() -> None:
    """Random worlds with clutter and speckle: motion is accurate or declined."""
    home_x, home_y = GRID.to_pixel(*HOME)
    confident = 0
    for seed in range(24):
        pick = random.Random(1000 + seed)
        speed, heading = pick.uniform(10, 90), pick.uniform(0, 360)
        result = nowcast(_world(seed, speed, heading, 0.0008, 0.0005), home_x, home_y, PIXEL_KM)
        assert result.status in ("ok", "uncertain")
        if result.status != "ok":
            continue
        confident += 1
        assert abs(result.speed_kmh - speed) < 15
        assert abs((result.heading - heading + 180) % 360 - 180) < 15
    assert confident >= 20


def test_eta_for_approaching_cell() -> None:
    """A cell edge 12 km west moving east at 36 km/h arrives in about 20 min."""
    home_x, home_y = GRID.to_pixel(*HOME)
    frames = {valid: decode_frame(cell_frame(valid))[1] for valid in frame_times()}
    result = nowcast(frames, home_x, home_y, PIXEL_KM)
    assert result.status == "ok"
    assert 30 <= result.speed_kmh <= 42
    assert result.heading_direction == "e"
    assert 10 <= result.expected_in <= 30
    assert result.next_hour_max > 0
    assert len(result.forecast) == 13


def test_no_rain_and_short_history() -> None:
    """No rain gives a firm zero; too little history declines."""
    home_x, home_y = GRID.to_pixel(*HOME)
    dry = {t: np.full((GRID.height, GRID.width), NO_ECHO, np.int8) for t in frame_times()}
    result = nowcast(dry, home_x, home_y, PIXEL_KM)
    assert result.status == "no_rain"
    assert result.next_hour_max == 0.0

    recent = {valid: decode_frame(cell_frame(valid))[1] for valid in frame_times()[-3:]}
    assert nowcast(recent, home_x, home_y, PIXEL_KM).status == "insufficient_history"


def test_raining_now_and_countdown() -> None:
    """Rain at home is 0 even without usable motion; minutes count from now, not the frame."""
    home_x, home_y = GRID.to_pixel(*HOME)
    times = frame_times()
    frames = {valid: decode_frame(cell_frame(valid, rain_at_home=True))[1] for valid in times[-2:]}
    result = nowcast(frames, home_x, home_y, PIXEL_KM)
    assert result.status == "insufficient_history"
    assert result.expected_in == 0

    frames = {valid: decode_frame(cell_frame(valid))[1] for valid in times}
    result = nowcast(frames, home_x, home_y, PIXEL_KM)
    assert result.arrival == times[-1] + timedelta(minutes=result.expected_in)
    late = times[-1] + timedelta(minutes=8)  # SMHI frames are a few minutes old
    assert result.minutes_until(late) == max(0, result.expected_in - 8)
    assert result.minutes_until(times[-1] + timedelta(hours=2)) == 0


def test_gap_is_filled_from_upstream_and_otherwise_unknown() -> None:
    """Rain seen upstream 10 minutes ago is carried into a gap; untraceable gap stays unknown."""
    from custom_components.smhi_radar.nowcast import WINDOW, Motion, fill_gap

    newest = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
    covered = np.ones((WINDOW, WINDOW), bool)
    covered[:, 80:100] = False  # a gap band
    old = np.zeros((WINDOW, WINDOW), np.float32)
    old[70:75, 70:75] = 2.0  # rain west of the gap 10 minutes ago
    now = np.zeros((WINDOW, WINDOW), np.float32)
    fields = {newest - timedelta(minutes=10): old, newest: now}
    values, filled = fill_gap(fields, covered, newest, Motion(1.2, 0.0, 10))  # 12 px east in 10 min
    assert values[72, 84] == 2.0 and filled[72, 84]
    assert np.isnan(values[72, 99])  # came from inside the gap: unknown
    assert values[72, 60] == 0.0 and not filled[72, 60]  # covered ground is measured
    unknown, _ = fill_gap(fields, covered, newest, None)
    assert np.isnan(unknown[:, 80:100]).all()


def test_nearby_is_unknown_not_dry_next_to_a_gap() -> None:
    """Dry where measured but a gap within the radius gives unknown; rain anywhere still counts."""
    from custom_components.smhi_radar.analysis import nearby_rain

    home_x, home_y = GRID.to_pixel(*HOME)
    rate = np.zeros((GRID.height, GRID.width), np.float32)
    x, y = int(home_x), int(home_y)
    rate[y - 2 : y + 3, x + 2 : x + 6] = np.nan  # gap just east of home
    result = nearby_rain(rate, GRID, *HOME, 10, 100)
    assert result.raining is None and result.nearest_km is None
    rate[y, x - 3] = 1.5  # rain 6 km west
    result = nearby_rain(rate, GRID, *HOME, 10, 100)
    assert result.raining is True
    assert result.nearest_km is None  # the gap is closer than that rain
