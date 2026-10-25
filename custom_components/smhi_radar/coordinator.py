"""Data update coordinator for SMHI Weather Radar."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .analysis import in_coverage
from .api import SmhiRadarClient, frame_url
from .const import (
    CONF_ANIMATE,
    CONF_LOCAL_RADIUS,
    CONF_NEARBY_RADIUS,
    CONF_PERSONS,
    CONF_UPDATE_INTERVAL,
    CONF_USE_HOME_LOCATION,
    DEFAULT_ANIMATE,
    DEFAULT_LOCAL_RADIUS,
    DEFAULT_NEARBY_RADIUS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    FORECAST_MAX_AGE_HOURS,
    FORECAST_REFRESH_MINUTES,
    FRAME_INTERVAL_MINUTES,
    HISTORY_MINUTES,
    PERSON_RENDER_COOLDOWN,
    PERSON_RENDER_COOLDOWN_ANIMATED,
    RADAR_STATUS_MAX_AGE_HOURS,
    RADAR_STATUS_REFRESH_MINUTES,
    WARNINGS_MAX_AGE_HOURS,
    WARNINGS_REFRESH_MINUTES,
)
from .exceptions import SmhiRadarError
from .coverage import coverage, online_radars, parse_status
from .forecast import Outlook, PrecipitationStep, classify, expected_type, outlook, parse_forecast
from .ibw import Warning, for_location, parse_warnings
from .models import Analysis, Location, RadarData, RadarMetadata
from .nowcast import LEAD_MAX
from .pipeline import Settings, StoredFrame, analyse, decode, render
from .render import Marker

if TYPE_CHECKING:
    from . import SmhiRadarConfigEntry

_LOGGER = logging.getLogger(__name__)

BACKFILL_PARALLEL = 4
NOWCAST_CONFIDENT = ("ok", "no_rain")
BACKFILL_TIMEOUT = 25


class SmhiRadarCoordinator(DataUpdateCoordinator[RadarData]):
    """Fetch radar frames, keep the last hour, analyse and render."""

    config_entry: SmhiRadarConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SmhiRadarConfigEntry, client: SmhiRadarClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
            ),
        )
        self.client = client
        self.last_error: str | None = None
        self.failed_update_count = 0
        self._frames: dict[datetime, StoredFrame] = {}
        self._unavailable: set[datetime] = set()
        self._markers: tuple[Marker, ...] = ()
        self._forecast: list[PrecipitationStep] = []
        self._forecast_fetched: datetime | None = None
        self._forecast_location: tuple[float, float] | None = None
        self._warnings: list[Warning] = []
        self._warnings_fetched: datetime | None = None
        self._radar_status: dict[str, datetime] = {}
        self._status_fetched: datetime | None = None
        self._analysed_online: frozenset[str] | None = None
        self._render_lock = asyncio.Lock()
        self._person_debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=PERSON_RENDER_COOLDOWN_ANIMATED
            if self.settings.animate
            else PERSON_RENDER_COOLDOWN,
            immediate=True,
            function=self._async_rerender,
        )

    # ------------------------------------------------------------------
    # Settings and markers

    @property
    def settings(self) -> Settings:
        """Return the current settings."""
        options = self.config_entry.options
        latitude, longitude = self.coordinates()
        return Settings(
            latitude=latitude,
            longitude=longitude,
            nearby_km=float(options.get(CONF_NEARBY_RADIUS, DEFAULT_NEARBY_RADIUS)),
            local_km=float(options.get(CONF_LOCAL_RADIUS, DEFAULT_LOCAL_RADIUS)),
            animate=bool(options.get(CONF_ANIMATE, DEFAULT_ANIMATE)),
        )

    @property
    def persons(self) -> list[str]:
        """Return the person entities shown on the map."""
        return list(self.config_entry.options.get(CONF_PERSONS, []))

    def coordinates(self) -> tuple[float, float]:
        """Return the configured location."""
        options = self.config_entry.options
        if options.get(CONF_USE_HOME_LOCATION, True):
            return self.hass.config.latitude, self.hass.config.longitude
        return options[CONF_LATITUDE], options[CONF_LONGITUDE]

    def _current_markers(self) -> tuple[Marker, ...]:
        """Return person markers (with a position) and the location marker."""
        markers = []
        for entity_id in self.persons:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            latitude = state.attributes.get(ATTR_LATITUDE)
            longitude = state.attributes.get(ATTR_LONGITUDE)
            if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
                # About 10 m precision; avoids re-rendering on GPS jitter.
                markers.append(Marker("person", round(latitude, 4), round(longitude, 4)))
        latitude, longitude = self.coordinates()
        markers.append(Marker("home", latitude, longitude))
        return tuple(markers)

    @callback
    def async_track_persons(self) -> CALLBACK_TYPE:
        """Re-render when a tracked person moves; returns an unsubscribe."""
        unsubscribe = (
            async_track_state_change_event(self.hass, self.persons, self._handle_person)
            if self.persons
            else None
        )

        @callback
        def _stop() -> None:
            if unsubscribe:
                unsubscribe()
            self._person_debouncer.async_shutdown()

        return _stop

    @callback
    def _handle_person(self, event: Event[EventStateChangedData]) -> None:
        if self.data is not None and self._current_markers() != self._markers:
            self.config_entry.async_create_background_task(
                self.hass, self._person_debouncer.async_call(), f"{DOMAIN}_person_render"
            )

    async def _async_rerender(self) -> None:
        """Redraw the images for new person positions (no new frame)."""
        async with self._render_lock:
            data = self.data
            markers = self._current_markers()
            if data is None or markers == self._markers or not self._frames:
                return
            national, local = await self._async_render(markers)
            self.data = replace(
                data, national_image=national, local_image=local, rendered=dt_util.utcnow()
            )
        # Not async_set_updated_data: that would reschedule the next poll.
        self.async_update_listeners()

    # ------------------------------------------------------------------
    # Updates

    async def _async_update_data(self) -> RadarData:
        """Fetch new frames; keep the last data through short outages."""
        settings = self.settings
        if not in_coverage(settings.latitude, settings.longitude):
            # Not recoverable by retrying; stops the setup retry loop.
            raise ConfigEntryError(translation_domain=DOMAIN, translation_key="outside_coverage")

        try:
            metadata = await self.client.async_get_latest_metadata()
            await self._async_collect(metadata)
        except SmhiRadarError as err:
            self.last_error = str(err)
            self.failed_update_count += 1
            if self.data is None:
                raise UpdateFailed(str(err)) from err
            if self.failed_update_count == 1:
                _LOGGER.warning("SMHI radar update failed, keeping the last frame: %s", err)
            return self.data

        if self.failed_update_count:
            _LOGGER.info("SMHI radar updates recovered")
        self.last_error = None
        self.failed_update_count = 0
        await self._async_refresh_forecast(settings.latitude, settings.longitude)
        await self._async_refresh_warnings()
        await self._async_refresh_radar_status()

        current = self.data
        markers = self._current_markers()
        online = self._online(dt_util.utcnow())
        if (
            current is not None
            and current.metadata.valid == metadata.valid
            and (current.latitude, current.longitude) == (settings.latitude, settings.longitude)
            and markers == self._markers
            and online == self._analysed_online
        ):
            context = self._context(current.analysis, settings.latitude, settings.longitude)
            if all(getattr(current, key) == value for key, value in context.items()):
                return current
            return replace(current, **context)

        async with self._render_lock:
            frames = self._history()
            analysis = await self.hass.async_add_executor_job(analyse, frames, settings, online)
            self._analysed_online = online
            national, local = await self._async_render(markers)
        return RadarData(
            metadata=metadata,
            image=frames[-1].png,
            national_image=national,
            local_image=local,
            rendered=dt_util.utcnow(),
            analysis=analysis,
            latitude=settings.latitude,
            longitude=settings.longitude,
            frame_count=len(frames),
            **self._context(analysis, settings.latitude, settings.longitude),
        )

    # ------------------------------------------------------------------
    # Forecast and warnings (sensors only)

    async def _async_refresh_forecast(self, latitude: float, longitude: float) -> None:
        """Fetch the point forecast when due; failures keep the last one."""
        now = dt_util.utcnow()
        due = (
            self._forecast_fetched is None
            or now - self._forecast_fetched >= timedelta(minutes=FORECAST_REFRESH_MINUTES)
            or self._forecast_location != (latitude, longitude)
        )
        if not due:
            return
        try:
            self._forecast = parse_forecast(await self.client.async_get_forecast(latitude, longitude))
        except SmhiRadarError as err:
            _LOGGER.debug("SMHI forecast not updated: %s", err)
            if self._forecast_location != (latitude, longitude):
                self._forecast = []
            return
        self._forecast_fetched = now
        self._forecast_location = (latitude, longitude)

    async def _async_refresh_warnings(self) -> None:
        """Fetch the warnings feed when due; failures keep the last one."""
        now = dt_util.utcnow()
        if self._warnings_fetched and now - self._warnings_fetched < timedelta(
            minutes=WARNINGS_REFRESH_MINUTES
        ):
            return
        try:
            self._warnings = parse_warnings(await self.client.async_get_warnings())
        except SmhiRadarError as err:
            _LOGGER.debug("SMHI warnings not updated: %s", err)
            return
        self._warnings_fetched = now

    async def _async_refresh_radar_status(self) -> None:
        """Fetch per-radar delivery times when due; failures keep the last."""
        now = dt_util.utcnow()
        if self._status_fetched and now - self._status_fetched < timedelta(
            minutes=RADAR_STATUS_REFRESH_MINUTES
        ):
            return
        try:
            self._radar_status = parse_status(await self.client.async_get_radar_status())
        except SmhiRadarError as err:
            _LOGGER.debug("SMHI radar status not updated: %s", err)
            return
        self._status_fetched = now

    def _online(self, now: datetime) -> frozenset[str] | None:
        """Return the delivering radars, or None while the status is unknown."""
        if self._status_fetched is None or now - self._status_fetched > timedelta(
            hours=RADAR_STATUS_MAX_AGE_HOURS
        ):
            return None
        return online_radars(self._radar_status, now)

    def _context(self, analysis: Analysis, latitude: float, longitude: float) -> dict[str, Any]:
        """Combine radar (measured or traced into gaps) with forecast and warnings."""
        now = dt_util.utcnow()
        steps = self._forecast
        if self._forecast_fetched is None or now - self._forecast_fetched > timedelta(
            hours=FORECAST_MAX_AGE_HOURS
        ):
            steps = []
        area = None
        if self._online(now) is not None:
            area = coverage(self._radar_status, latitude, longitude, now)

        nowcast = analysis.nowcast
        if nowcast.arrival is not None:
            after = None  # the radar already expects precipitation
        elif nowcast.valid is not None and nowcast.status == "no_rain":
            after = max(now, nowcast.valid + timedelta(minutes=LEAD_MAX))
        elif nowcast.valid is not None and nowcast.status == "ok" and nowcast.horizon is not None:
            # Radar sees nothing arriving as far as it can follow; look beyond.
            after = max(now, nowcast.valid + timedelta(minutes=nowcast.horizon))
        else:
            after = now
        future = outlook(steps, now, after)
        location = _location(analysis, future)
        wet = None
        if location.intensity is not None or location.raining_nearby is not None:
            wet = bool(location.intensity) or bool(location.raining_nearby)
        minutes = (
            None if location.arrival is None else max(0, round((location.arrival - now).total_seconds() / 60))
        )

        warnings = None
        if self._warnings_fetched and now - self._warnings_fetched <= timedelta(
            hours=WARNINGS_MAX_AGE_HOURS
        ):
            warnings = tuple(for_location(self._warnings, latitude, longitude, now))
        return {
            "precipitation_type": classify(steps, now, wet),
            "expected_type": expected_type(steps, now, minutes),
            "outlook": future,
            "warnings": warnings,
            "coverage": area,
            "location": location,
        }

    def _history(self) -> list[StoredFrame]:
        return [self._frames[valid] for valid in sorted(self._frames)]

    async def _async_render(self, markers: tuple[Marker, ...]) -> tuple[bytes, bytes]:
        """Render with the current history; caller holds the render lock."""
        frames = self._history()
        stamps = {f.valid: dt_util.as_local(f.valid).strftime("%Y-%m-%d %H:%M") for f in frames}
        images = await self.hass.async_add_executor_job(
            render, frames, self.settings, list(markers), stamps
        )
        self._markers = markers
        return images

    async def _async_collect(self, metadata: RadarMetadata) -> None:
        """Download the newest frame and backfill the last hour."""
        newest = metadata.valid
        if newest not in self._frames:
            png = await self.client.async_download_image(metadata.link)
            self._frames[newest] = await self.hass.async_add_executor_job(decode, newest, png)

        oldest = newest - timedelta(minutes=HISTORY_MINUTES)
        self._frames = {t: f for t, f in self._frames.items() if oldest <= t <= newest}
        self._unavailable = {t for t in self._unavailable if t >= oldest}
        wanted = [
            newest - timedelta(minutes=step)
            for step in range(FRAME_INTERVAL_MINUTES, HISTORY_MINUTES + 1, FRAME_INTERVAL_MINUTES)
        ]
        missing = [t for t in wanted if t not in self._frames and t not in self._unavailable]
        if missing:
            await self._async_backfill(missing)

    async def _async_backfill(self, missing: list[datetime]) -> None:
        """Fetch older frames; frames that fail are not retried."""
        semaphore = asyncio.Semaphore(BACKFILL_PARALLEL)

        async def fetch(valid: datetime) -> tuple[datetime, bytes | None]:
            async with semaphore:
                try:
                    return valid, await self.client.async_download_image(
                        frame_url(f"radar_{valid:%y%m%d%H%M}", valid)
                    )
                except SmhiRadarError:
                    return valid, None

        try:
            async with asyncio.timeout(BACKFILL_TIMEOUT):
                results = await asyncio.gather(*(fetch(valid) for valid in missing))
        except TimeoutError:
            _LOGGER.debug("Radar history backfill timed out; continuing with what is there")
            return

        for valid, png in results:
            if png is None:
                self._unavailable.add(valid)
                continue
            try:
                self._frames[valid] = await self.hass.async_add_executor_job(decode, valid, png)
            except SmhiRadarError:
                self._unavailable.add(valid)


def _location(analysis: Analysis, future: Outlook) -> Location:
    """Radar first (measured, then traced into gaps), the forecast where it cannot see."""
    reading, nearby, nowcast = analysis.reading, analysis.nearby, analysis.nowcast
    intensity, intensity_source = reading.intensity, analysis.reading_source
    if intensity is None and future.now_rate is not None:
        intensity, intensity_source = future.now_rate, "forecast"
    raining, nearby_source = nearby.raining, analysis.nearby_source
    if raining is None and future.now_likely is not None:
        raining, nearby_source = future.now_likely, "forecast"
    next_hour, next_hour_source = None, None
    if nowcast.status in NOWCAST_CONFIDENT and nowcast.next_hour_max is not None:
        next_hour, next_hour_source = nowcast.next_hour_max, "radar"
    elif future.hour_max_rate is not None:
        next_hour, next_hour_source = future.hour_max_rate, "forecast"
    if nowcast.arrival is not None:
        arrival, arrival_source = nowcast.arrival, "radar"
    elif future.arrival is not None:
        arrival, arrival_source = future.arrival, "forecast"
    else:
        arrival, arrival_source = None, None
    radar_nearby = nearby_source != "forecast"
    return Location(
        intensity=None if intensity is None else round(intensity, 2),
        intensity_source=intensity_source if intensity is not None else None,
        reflectivity=reading.reflectivity,
        raining_nearby=raining,
        nearby_max=nearby.max_intensity if radar_nearby else None,
        nearest_km=nearby.nearest_km if radar_nearby else None,
        nearest_bearing=nearby.nearest_bearing if radar_nearby else None,
        nearest_direction=nearby.nearest_direction if radar_nearby else None,
        nearby_source=nearby_source if raining is not None else None,
        next_hour_max=next_hour,
        next_hour_source=next_hour_source,
        arrival=arrival,
        arrival_source=arrival_source,
    )
