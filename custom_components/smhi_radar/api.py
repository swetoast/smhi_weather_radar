"""Async client and response parsing for the SMHI radar API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import API_HOST, API_URL, DEFAULT_TIMEOUT, FORECAST_URL, RADAR_STATUS_URL, WARNINGS_URL
from .exceptions import SmhiRadarConnectionError, SmhiRadarDataError, SmhiRadarError
from .models import RadarMetadata


def parse_updated(value: Any) -> datetime:
    """Parse an ISO 8601 update timestamp."""
    if not isinstance(value, str) or not value:
        raise SmhiRadarDataError("Missing SMHI update timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise SmhiRadarDataError("Invalid SMHI update timestamp") from err
    if parsed.tzinfo is None:
        raise SmhiRadarDataError("SMHI update timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def parse_valid(value: Any) -> datetime:
    """Parse an observation time ("YYYY-MM-DD HH:MM", UTC)."""
    if not isinstance(value, str) or not value:
        raise SmhiRadarDataError("Missing SMHI observation timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    except ValueError as err:
        raise SmhiRadarDataError("Invalid SMHI observation timestamp") from err


def _check_url(link: Any) -> str:
    """Only allow HTTPS links to the SMHI radar host."""
    if not isinstance(link, str) or not link:
        raise SmhiRadarDataError("SMHI PNG link is missing")
    parsed = urlparse(link)
    if parsed.scheme != "https" or parsed.hostname != API_HOST:
        raise SmhiRadarDataError("SMHI returned an unsafe PNG link")
    return link


def frame_url(key: str, valid: datetime) -> str:
    """Return the fixed URL of one frame.

    The status endpoint links to a moving latest.png, which can already hold
    a newer frame than the metadata describes. The dated URL always matches.
    """
    return f"{API_URL}/{valid:%Y/%m/%d}/{key}.png"


def parse_latest_png(payload: Any) -> RadarMetadata:
    """Return metadata for the newest PNG frame in a status response."""
    if not isinstance(payload, dict):
        raise SmhiRadarDataError("SMHI metadata is not an object")
    if payload.get("timeZone") != "UTC":
        raise SmhiRadarDataError("SMHI returned an unexpected timezone")
    api_updated = parse_updated(payload.get("updated"))
    last_files = payload.get("lastFiles")
    if not isinstance(last_files, list):
        raise SmhiRadarDataError("SMHI metadata has no valid lastFiles list")

    candidates: list[RadarMetadata] = []
    for radar_file in last_files:
        if not isinstance(radar_file, dict) or not isinstance(radar_file.get("formats"), list):
            continue
        key = radar_file.get("key")
        if not isinstance(key, str) or not key:
            continue
        for radar_format in radar_file["formats"]:
            if not isinstance(radar_format, dict) or radar_format.get("key") != "png":
                continue
            _check_url(radar_format.get("link"))
            valid = parse_valid(radar_file.get("valid"))
            candidates.append(
                RadarMetadata(
                    key=key,
                    valid=valid,
                    file_updated=parse_updated(radar_file.get("updated")),
                    format_updated=parse_updated(radar_format.get("updated")),
                    link=frame_url(key, valid),
                    api_updated=api_updated,
                )
            )
    if not candidates:
        raise SmhiRadarDataError("No current PNG radar image is available")
    return max(candidates, key=lambda item: (item.valid, item.format_updated, item.key))


class SmhiRadarClient:
    """Client for the SMHI radar service."""

    def __init__(self, session: ClientSession) -> None:
        """Initialize the client."""
        self._session = session
        self._timeout = ClientTimeout(total=DEFAULT_TIMEOUT)

    async def async_get_latest_metadata(self) -> RadarMetadata:
        """Fetch metadata for the newest frame."""
        try:
            async with self._session.get(
                API_URL,
                params={"format": "png", "timeZone": "UTC"},
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                return parse_latest_png(await response.json(content_type=None))
        except SmhiRadarError:
            raise
        except (TimeoutError, ClientError, ValueError) as err:
            raise SmhiRadarConnectionError("Could not retrieve SMHI radar metadata") from err

    async def async_download_image(self, url: str) -> bytes:
        """Download one frame."""
        _check_url(url)
        try:
            async with self._session.get(
                url,
                headers={"Accept": "image/png"},
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                if response.url.scheme != "https" or response.url.host != API_HOST:
                    raise SmhiRadarDataError("SMHI image redirected to an unsafe host")
                data = await response.read()
        except SmhiRadarError:
            raise
        except (TimeoutError, ClientError) as err:
            raise SmhiRadarConnectionError("Could not download the SMHI radar image") from err
        if not data:
            raise SmhiRadarDataError("SMHI returned an empty radar image")
        return data

    async def async_get_forecast(self, latitude: float, longitude: float) -> Any:
        """Fetch the snow1g point forecast (raw JSON)."""
        url = FORECAST_URL.format(lat=round(latitude, 4), lon=round(longitude, 4))
        try:
            async with self._session.get(
                url, headers={"Accept": "application/json"}, timeout=self._timeout
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except (TimeoutError, ClientError, ValueError) as err:
            raise SmhiRadarConnectionError("Could not retrieve the SMHI forecast") from err

    async def async_get_warnings(self) -> Any:
        """Fetch the impact-based warnings feed (raw JSON)."""
        try:
            async with self._session.get(
                WARNINGS_URL, headers={"Accept": "application/json"}, timeout=self._timeout
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except (TimeoutError, ClientError, ValueError) as err:
            raise SmhiRadarConnectionError("Could not retrieve SMHI warnings") from err

    async def async_get_radar_status(self) -> Any:
        """Fetch when each radar last delivered data (raw JSON)."""
        try:
            async with self._session.get(
                RADAR_STATUS_URL, headers={"Accept": "application/json"}, timeout=self._timeout
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except (TimeoutError, ClientError, ValueError) as err:
            raise SmhiRadarConnectionError("Could not retrieve SMHI radar status") from err
