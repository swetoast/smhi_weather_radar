"""Data models for SMHI Weather Radar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .analysis import NearbyRain, PointReading
from .coverage import Coverage
from .forecast import Outlook, PrecipitationType
from .ibw import Warning
from .nowcast import Nowcast


@dataclass(frozen=True, slots=True)
class RadarMetadata:
    """Metadata for one SMHI radar frame."""

    key: str
    valid: datetime
    file_updated: datetime
    format_updated: datetime
    link: str
    api_updated: datetime


@dataclass(frozen=True, slots=True)
class Analysis:
    """Everything derived from the frames for the configured location."""

    reading: PointReading
    nearby: NearbyRain
    nowcast: Nowcast
    # "measured", "extrapolated" (traced into a radar gap) or None (unknown).
    reading_source: str | None = "measured"
    nearby_source: str | None = "measured"
    covered: bool = True  # a working radar measures the location itself


@dataclass(frozen=True, slots=True)
class Location:
    """Final values at the location, each with where it came from.

    Sources: "measured" (radar), "extrapolated" (radar traced into a gap),
    "forecast" (SMHI point forecast, where the radar cannot see), or None.
    """

    intensity: float | None
    intensity_source: str | None
    reflectivity: int | None
    raining_nearby: bool | None
    nearby_max: float | None
    nearest_km: float | None
    nearest_bearing: float | None
    nearest_direction: str | None
    nearby_source: str | None
    next_hour_max: float | None
    next_hour_source: str | None
    arrival: datetime | None
    arrival_source: str | None


@dataclass(frozen=True, slots=True)
class RadarData:
    """Complete state published to entities."""

    metadata: RadarMetadata
    image: bytes
    national_image: bytes
    local_image: bytes
    rendered: datetime
    analysis: Analysis
    latitude: float
    longitude: float
    frame_count: int
    precipitation_type: PrecipitationType
    expected_type: str | None
    outlook: Outlook
    warnings: tuple[Warning, ...] | None  # None until the feed has been read
    coverage: Coverage | None  # None until the radar status has been read
    location: Location
