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

    @property
    def measured(self) -> bool:
        """Return False when no working radar covers the location."""
        return self.coverage is None or self.coverage.covered
