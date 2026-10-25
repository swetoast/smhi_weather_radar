"""Short term precipitation nowcast by motion extrapolation.

Motion: the rain field around the location is compared between every pair
of frames 10 to 30 minutes apart in the last half hour. Fields are log
scaled and smoothed first, because small rain cells grow and decay within
minutes while the larger pattern keeps moving; correlating raw intensities
followed that noise instead of the wind. Each pair gives a velocity; the
largest group of agreeing velocities wins, so a few bad pairs no longer
throw the whole estimate away.

Arrival: the newest frame is moved forward along that velocity minute by
minute and the location is checked within ARRIVAL_RADIUS_KM (a single
pixel is about 2 km, so a shower passing just beside the house was missed).
The arrival is an absolute time, so sensors can count down between radar
frames and allow for the age of the newest frame.

Radar gaps: pixels no working radar measures (see coverage) are unknown,
not dry. Where the motion is known, a gap pixel takes the value its rain
had when it was last upstream over covered ground, up to MAX_FILL_AGE
minutes back. Trajectories that run into unknown ground stop there.

Small spots that are wet in nearly every frame are treated as static
clutter and ignored. When the motion is not trustworthy the status is
"uncertain", but rain already at the location is still reported (0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
from typing import Final

import numpy as np

from .analysis import NO_DATA, compass_point, rain_rate_grid

WINDOW: Final = 160  # pixels (about 320 km) around the location
LEAD_MAX: Final = 60  # minutes
FORECAST_STEP: Final = 5  # minutes between forecast attribute entries
ARRIVAL_RADIUS_KM: Final = 3.0
MIN_RAIN_PIXELS: Final = 25
MIN_PEAK: Final = 6.0  # correlation peak height in standard deviations
SMOOTH_PX: Final = 1.0  # Gaussian smoothing before correlation
WHITENING: Final = 0.8  # 1.0 is classic phase correlation, 0 is plain correlation
REMOVE_STATIC: Final = True  # subtract the hour's mean field: clutter and ground echoes stop pinning motion to zero
PAIR_LAGS: Final = (10, 15, 20, 25, 30)  # minutes
PAIR_SPAN: Final = 30  # only frames this recent are paired
MAX_FILL_AGE: Final = 30  # minutes; simulated accuracy of gap filling falls below 85 % after this
MIN_AGREEING: Final = 3
AGREE_PX_PER_MIN: Final = 0.08  # about 10 km/h at 2 km pixels
AGREE_SHARE: Final = 0.3  # or this share of the speed
MAX_SPEED_KMH: Final = 150.0
CLUTTER_SHARE: Final = 0.8  # wet in this share of the hour = persistent
CLUTTER_MIN_FRAMES: Final = 6
CLUTTER_MAX_NEIGHBOURS: Final = 6  # persistent pixels in a 5x5 box to count as a spot


@dataclass(frozen=True, slots=True)
class Motion:
    """Rain field motion in pixels per minute (x right, y down)."""

    dx: float
    dy: float
    peak: float


@dataclass(frozen=True, slots=True, eq=False)
class LocalField:
    """Rain rates around the location for the newest frame.

    values is NaN where no radar measured and nothing could be traced in;
    filled marks pixels traced in from older frames. x0, y0 place the
    window in the composite grid.
    """

    values: np.ndarray
    filled: np.ndarray
    x0: int
    y0: int


@dataclass(frozen=True, slots=True)
class Nowcast:
    """Nowcast for the location.

    status is one of: "ok", "no_rain" (no rain anywhere near, nothing
    expected), "uncertain" (rain around but motion is not reliable) or
    "insufficient_history". expected_in counts minutes from the newest
    frame (valid); arrival is the same moment as a timestamp.
    """

    status: str
    valid: datetime | None = None
    expected_in: int | None = None
    next_hour_max: float | None = None
    speed_kmh: float | None = None
    heading: float | None = None
    heading_direction: str | None = None
    pairs_used: int | None = None
    forecast: list[dict[str, float | int | None]] = field(default_factory=list)
    horizon: int | None = None  # minutes the trajectory could be followed before a radar gap
    local: LocalField | None = field(default=None, compare=False, repr=False)

    @property
    def arrival(self) -> datetime | None:
        """Return when precipitation is expected at the location."""
        if self.valid is None or self.expected_in is None:
            return None
        return self.valid + timedelta(minutes=self.expected_in)

    def minutes_until(self, now: datetime) -> int | None:
        """Return whole minutes from now until arrival; 0 once it is due."""
        arrival = self.arrival
        if arrival is None:
            return None
        return max(0, math.ceil((arrival - now).total_seconds() / 60))


def crop(grid: np.ndarray, cx: int, cy: int, fill: int | float) -> np.ndarray:
    """Return a WINDOW x WINDOW cut centred on (cx, cy), padded with fill."""
    half = WINDOW // 2
    out = np.full((WINDOW, WINDOW), fill, dtype=grid.dtype)
    height, width = grid.shape
    x0, y0 = cx - half, cy - half
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(width, x0 + WINDOW), min(height, y0 + WINDOW)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = grid[sy0:sy1, sx0:sx1]
    return out


def _subpixel(values: np.ndarray, index: int) -> float:
    """Return a parabolic sub pixel offset around a peak (wrapping)."""
    left = values[(index - 1) % values.size]
    centre = values[index]
    right = values[(index + 1) % values.size]
    denominator = left - 2 * centre + right
    if denominator >= 0:
        return 0.0
    return float(0.5 * (left - right) / denominator)


_TAPER: Final = np.outer(np.hanning(WINDOW), np.hanning(WINDOW)).astype(np.float32)
_FREQ: Final = np.fft.fftfreq(WINDOW)
_SMOOTH: Final = np.exp(
    -2 * (math.pi * SMOOTH_PX) ** 2 * (_FREQ[:, None] ** 2 + _FREQ[None, :] ** 2)
)


def _spectrum(rate: np.ndarray, static: np.ndarray | None = None) -> np.ndarray | None:
    """Return the smoothed, tapered spectrum of a log scaled rain field."""
    if np.count_nonzero(rate) < MIN_RAIN_PIXELS:
        return None
    values = np.log1p(rate)
    if static is not None:
        values = values - static
    return np.fft.fft2(values * _TAPER) * _SMOOTH


def estimate_shift(older: np.ndarray | None, newer: np.ndarray | None) -> Motion | None:
    """Return the shift (pixels) moving the older spectrum onto the newer one."""
    if older is None or newer is None:
        return None
    cross = newer * np.conj(older)
    if WHITENING:
        magnitude = np.abs(cross) ** WHITENING
        magnitude[magnitude < 1e-12] = 1e-12
        cross = cross / magnitude
    surface = np.fft.ifft2(cross).real
    peak_index = int(np.argmax(surface))
    row, col = divmod(peak_index, WINDOW)
    spread = float(surface.std())
    if spread == 0:
        return None
    peak = (float(surface[row, col]) - float(surface.mean())) / spread
    if peak < MIN_PEAK:
        return None
    dy = row + _subpixel(surface[:, col], row)
    dx = col + _subpixel(surface[row, :], col)
    if dy > WINDOW / 2:
        dy -= WINDOW
    if dx > WINDOW / 2:
        dx -= WINDOW
    return Motion(dx, dy, peak)


def _box_count(mask: np.ndarray, radius: int) -> np.ndarray:
    """Return the number of True pixels in a (2r+1) square around each pixel."""
    size = 2 * radius + 1
    padded = np.pad(mask.astype(np.int32), radius + 1)
    total = padded.cumsum(axis=0).cumsum(axis=1)
    counts = total[size:, size:] - total[:-size, size:] - total[size:, :-size] + total[:-size, :-size]
    return counts[: mask.shape[0], : mask.shape[1]]


def _clutter_mask(fields: list[np.ndarray]) -> np.ndarray | None:
    """Return small spots that are wet in nearly every frame (static clutter)."""
    if len(fields) < CLUTTER_MIN_FRAMES:
        return None
    persistent = np.sum([f > 0 for f in fields], axis=0) >= CLUTTER_SHARE * len(fields)
    return persistent & (_box_count(persistent, 2) <= CLUTTER_MAX_NEIGHBOURS)


def combine(estimates: list[tuple[Motion, int]]) -> tuple[Motion, int] | None:
    """Return the weighted mean of the largest agreeing group of velocities."""
    best: list[tuple[Motion, int]] = []
    for candidate, _ in estimates:
        speed = math.hypot(candidate.dx, candidate.dy)
        tolerance = max(AGREE_PX_PER_MIN, AGREE_SHARE * speed)
        group = [
            (motion, lag)
            for motion, lag in estimates
            if math.hypot(motion.dx - candidate.dx, motion.dy - candidate.dy) <= tolerance
        ]
        if len(group) > len(best):
            best = group
    if len(best) < MIN_AGREEING or len(best) * 2 <= len(estimates):
        return None
    # Longer lags resolve velocity better; clearer peaks are more reliable.
    weights = [motion.peak * lag for motion, lag in best]
    total = sum(weights)
    dx = sum(m.dx * w for (m, _), w in zip(best, weights, strict=True)) / total
    dy = sum(m.dy * w for (m, _), w in zip(best, weights, strict=True)) / total
    return Motion(dx, dy, min(m.peak for m, _ in best)), len(best)


def _disc(radius_px: float) -> tuple[np.ndarray, np.ndarray]:
    reach = math.ceil(radius_px)
    oy, ox = np.mgrid[-reach : reach + 1, -reach : reach + 1]
    inside = np.hypot(ox, oy) <= radius_px
    return ox[inside], oy[inside]


def _near(field: np.ndarray, x: float, y: float, disc: tuple[np.ndarray, np.ndarray]) -> float | None:
    """Return the highest value within the disc around (x, y).

    NaN marks pixels no radar measured. Precipitation anywhere in the disc
    counts; otherwise the answer is only "dry" when the whole disc is known.
    """
    xs = np.floor(x).astype(int) + disc[0]
    ys = np.floor(y).astype(int) + disc[1]
    inside = (xs >= 0) & (xs < WINDOW) & (ys >= 0) & (ys < WINDOW)
    if not inside.any():
        return 0.0
    values = field[ys[inside], xs[inside]]
    known = values[~np.isnan(values)]
    if known.size and known.max() > 0:
        return float(known.max())
    if known.size < values.size:
        return None
    return 0.0


def fill_gap(
    fields: dict[datetime, np.ndarray],
    covered: np.ndarray,
    newest: datetime,
    motion: Motion | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the newest field with gap pixels filled, and where filling happened.

    A gap pixel takes the value its rain had at the most recent time it was
    upstream over covered ground, at most MAX_FILL_AGE minutes ago. What
    cannot be traced stays NaN (unknown).
    """
    values = np.where(covered, fields[newest], np.nan).astype(np.float32)
    filled = np.zeros(covered.shape, bool)
    if motion is None or covered.all():
        return values, filled
    ys, xs = np.nonzero(~covered)
    for moment in sorted(fields, reverse=True):
        age = (newest - moment).total_seconds() / 60
        if age <= 0 or age > MAX_FILL_AGE or xs.size == 0:
            continue
        sx = np.floor(xs + 0.5 - motion.dx * age).astype(int)
        sy = np.floor(ys + 0.5 - motion.dy * age).astype(int)
        inside = (sx >= 0) & (sx < WINDOW) & (sy >= 0) & (sy < WINDOW)
        found = np.zeros(xs.size, bool)
        found[inside] = covered[sy[inside], sx[inside]]
        values[ys[found], xs[found]] = fields[moment][sy[found], sx[found]]
        filled[ys[found], xs[found]] = True
        xs, ys = xs[~found], ys[~found]
    return values, filled


def nowcast(
    frames: dict[datetime, np.ndarray],
    home_x: float,
    home_y: float,
    pixel_km: float,
    covered: np.ndarray | None = None,
) -> Nowcast:
    """Return a nowcast from dBZ grids (int8, see analysis) keyed by time.

    home_x and home_y are fractional pixel coordinates of the location and
    pixel_km is the pixel size. covered marks composite pixels a working
    radar measures (all when None). Only a window around the location is used.
    """
    if not frames:
        return Nowcast("insufficient_history")
    times = sorted(frames)
    newest = times[-1]
    cx, cy = math.floor(home_x), math.floor(home_y)
    half = WINDOW // 2
    wx, wy = home_x - (cx - half), home_y - (cy - half)
    disc = _disc(max(1.0, ARRIVAL_RADIUS_KM / pixel_km))
    window_covered = (
        np.ones((WINDOW, WINDOW), bool)
        if covered is None
        else crop(covered.astype(np.int8), cx, cy, 1).astype(bool)
    )

    clean = {
        t: np.nan_to_num(rain_rate_grid(crop(frames[t], cx, cy, NO_DATA)), nan=0.0)
        for t in times
    }
    clutter = _clutter_mask(list(clean.values()))
    if clutter is not None:
        clean = {t: np.where(clutter, 0.0, grid).astype(np.float32) for t, grid in clean.items()}
    current = clean[newest]

    def result(status: str, motion: Motion | None = None, **kwargs) -> Nowcast:
        values, filled = fill_gap(clean, window_covered, newest, motion)
        local = LocalField(values, filled, cx - half, cy - half)
        if motion is None:
            now = _near(values, wx, wy, disc)
            return Nowcast(status, valid=newest, expected_in=0 if now else None, local=local, **kwargs)
        return _extrapolate(status, newest, motion, values, wx, wy, disc, pixel_km, local, **kwargs)

    if np.count_nonzero(current) == 0 and window_covered.all():
        # Nothing within about 160 km in any direction.
        return Nowcast(
            "no_rain",
            valid=newest,
            next_hour_max=0.0,
            forecast=[{"minutes": m, "intensity": 0.0} for m in range(0, LEAD_MAX + 1, FORECAST_STEP)],
            local=LocalField(np.where(window_covered, current, np.nan), np.zeros_like(window_covered), cx - half, cy - half),
        )

    recent = [t for t in times if newest - t <= timedelta(minutes=PAIR_SPAN)]
    static = np.mean([np.log1p(grid) for grid in clean.values()], axis=0) if REMOVE_STATIC else None
    spectra = {t: _spectrum(clean[t], static) for t in recent}
    estimates: list[tuple[Motion, int]] = []
    for newer in recent:
        for lag in PAIR_LAGS:
            older = newer - timedelta(minutes=lag)
            if older not in spectra:
                continue
            shift = estimate_shift(spectra[older], spectra[newer])
            if shift is not None:
                estimates.append((Motion(shift.dx / lag, shift.dy / lag, shift.peak), lag))

    possible = sum(newer - timedelta(minutes=lag) in spectra for newer in recent for lag in PAIR_LAGS)
    combined = combine(estimates) if estimates else None
    speed_kmh = math.hypot(combined[0].dx, combined[0].dy) * pixel_km * 60 if combined else None
    if combined is None or speed_kmh is None or speed_kmh > MAX_SPEED_KMH:
        return result("uncertain" if possible >= MIN_AGREEING else "insufficient_history", pairs_used=0)
    motion, used = combined
    return result("ok", motion, pairs_used=used)


def _extrapolate(
    status: str,
    newest: datetime,
    motion: Motion,
    values: np.ndarray,
    wx: float,
    wy: float,
    disc: tuple[np.ndarray, np.ndarray],
    pixel_km: float,
    local: LocalField,
    **kwargs,
) -> Nowcast:
    """Move the (gap filled) field along the motion over the location."""
    intensities: list[float | None] = []
    expected_in = None
    for minutes in range(LEAD_MAX + 1):
        # Semi-Lagrangian: the rain arriving now was upstream minutes ago.
        value = _near(values, wx - motion.dx * minutes, wy - motion.dy * minutes, disc)
        intensities.append(value)
        if value is None:
            break  # the trajectory runs into ground no radar can see
        if value > 0 and expected_in is None:
            expected_in = minutes
    resolved = [value for value in intensities if value is not None]
    complete = len(resolved) == LEAD_MAX + 1
    peak = max(resolved, default=0.0)
    heading = math.degrees(math.atan2(motion.dx, -motion.dy)) % 360
    return Nowcast(
        status,
        valid=newest,
        expected_in=expected_in,
        next_hour_max=round(peak, 2) if complete or peak > 0 else None,
        speed_kmh=round(math.hypot(motion.dx, motion.dy) * pixel_km * 60, 1),
        heading=round(heading),
        heading_direction=compass_point(heading),
        forecast=[
            {"minutes": m, "intensity": None if m >= len(intensities) or intensities[m] is None else round(intensities[m], 2)}
            for m in range(0, LEAD_MAX + 1, FORECAST_STEP)
        ],
        horizon=len(resolved) - 1,
        local=local,
        **kwargs,
    )
