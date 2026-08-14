"""Provides the ezviz DataUpdateCoordinator."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from pyezvizapi.client import EzvizClient
from pyezvizapi.exceptions import (
    EzvizAuthTokenExpired,
    EzvizAuthVerificationCode,
    HTTPError,
    InvalidURL,
    PyEzvizError,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_MOTION_CLEAR_SECONDS, DEFAULT_MOTION_CLEAR_SECONDS, DOMAIN
from .push import (
    PUSH_MOTION_CLEAR_GRACE_SECONDS,
    PUSH_OVERLAY_SECONDS,
    normalize_motion_clear_seconds,
    normalize_push_event,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _RecentPush:
    """Short-lived MQTT override while the cloud polling API catches up."""

    event_id: str | None
    values: dict[str, Any]
    received_at: float
    motion_until: float
    expires_at: float


class EzvizDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching EZVIZ data."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api: EzvizClient,
        api_timeout: int,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize global EZVIZ data updater."""
        self.ezviz_client = api
        self._api_timeout = api_timeout
        self._entry_id = config_entry.entry_id
        self._known_serials: set[str] | None = None
        self._reload_scheduled = False
        self._recent_pushes: dict[str, _RecentPush] = {}
        self._motion_clear_handles: dict[str, asyncio.TimerHandle] = {}
        self._motion_clear_seconds = normalize_motion_clear_seconds(
            config_entry.options.get(
                CONF_MOTION_CLEAR_SECONDS,
                DEFAULT_MOTION_CLEAR_SECONDS,
            )
        )
        update_interval = timedelta(seconds=30)

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> dict:
        """Fetch data from EZVIZ."""
        try:
            async with asyncio.timeout(self._api_timeout):
                data = await self.hass.async_add_executor_job(self.ezviz_client.load_cameras)
                self._apply_recent_pushes(data)
                self._schedule_reload_for_new_devices(data)
                return data

        except (EzvizAuthTokenExpired, EzvizAuthVerificationCode) as error:
            raise ConfigEntryAuthFailed from error

        except TimeoutError as error:
            raise UpdateFailed("Timed out while updating EZVIZ data") from error

        except (InvalidURL, HTTPError, PyEzvizError) as error:
            raise UpdateFailed("Invalid response from EZVIZ API") from error

    def _schedule_reload_for_new_devices(self, data: dict) -> None:
        """Reload once when the account discovers an additional camera."""
        current_serials = set(data)
        if self._known_serials is None:
            self._known_serials = current_serials
            return

        new_serials = current_serials - self._known_serials
        self._known_serials = current_serials
        if not new_serials or self._reload_scheduled:
            return

        self._reload_scheduled = True
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self._entry_id),
            "Reload EZVIZ after discovering a camera",
        )

    def merge_mqtt_update(self, serial: str, mqtt_data: dict) -> None:
        """Merge an MQTT alarm immediately, before the cloud image finishes processing."""
        push_update = normalize_push_event(mqtt_data)
        if push_update is None:
            return

        now = self.hass.loop.time()
        previous = self._recent_pushes.get(serial)
        same_event = bool(
            previous and push_update.event_id and push_update.event_id == previous.event_id
        )
        values = dict(previous.values) if same_event and previous else {}
        values.update(push_update.values)
        received_at = previous.received_at if same_event and previous else now
        motion_until = (
            previous.motion_until if same_event and previous else now + self._motion_clear_seconds
        )
        self._recent_pushes[serial] = _RecentPush(
            event_id=push_update.event_id,
            values=values,
            received_at=received_at,
            motion_until=motion_until,
            expires_at=max(
                now + PUSH_OVERLAY_SECONDS,
                motion_until + PUSH_MOTION_CLEAR_GRACE_SECONDS,
            ),
        )

        self._schedule_motion_clear(serial, motion_until)
        self._broadcast_recent_push(serial, now)

    def _apply_recent_pushes(self, data: dict[str, dict[str, Any]]) -> None:
        """Keep recent push data over a briefly stale cloud polling response."""
        now = self.hass.loop.time()
        for serial, recent in list(self._recent_pushes.items()):
            if recent.expires_at <= now:
                self._recent_pushes.pop(serial, None)
                continue
            camera_data = dict(data.get(serial, {}))
            camera_data.update(self._recent_values(recent, now))
            data[serial] = camera_data

    @staticmethod
    def _recent_values(recent: _RecentPush, now: float) -> dict[str, Any]:
        """Return current motion state plus recent alarm metadata."""
        values = dict(recent.values)
        values["Motion_Trigger"] = now < recent.motion_until
        values["Seconds_Last_Trigger"] = max(0.0, now - recent.received_at)
        return values

    def _broadcast_recent_push(self, serial: str, now: float) -> None:
        """Publish one recent push snapshot to all coordinator listeners."""
        recent = self._recent_pushes.get(serial)
        if recent is None:
            return
        camera_data = dict((self.data or {}).get(serial, {}))
        camera_data.update(self._recent_values(recent, now))
        updated_data = dict(self.data or {})
        updated_data[serial] = camera_data
        self.async_set_updated_data(updated_data)

    def _schedule_motion_clear(self, serial: str, motion_until: float) -> None:
        """Clear the motion sensor after the configured active window."""
        if previous_handle := self._motion_clear_handles.pop(serial, None):
            previous_handle.cancel()
        delay = max(0.0, motion_until - self.hass.loop.time())
        self._motion_clear_handles[serial] = self.hass.loop.call_later(
            delay,
            self._clear_motion,
            serial,
        )

    def _clear_motion(self, serial: str) -> None:
        """Publish motion off without discarding the latest image metadata."""
        self._motion_clear_handles.pop(serial, None)
        recent = self._recent_pushes.get(serial)
        if recent is None:
            return
        now = self.hass.loop.time()
        if recent.motion_until > now:
            self._schedule_motion_clear(serial, recent.motion_until)
            return
        self._broadcast_recent_push(serial, now)

    async def async_shutdown(self) -> None:
        """Cancel push timers and shut down the coordinator."""
        for handle in self._motion_clear_handles.values():
            handle.cancel()
        self._motion_clear_handles.clear()
        self._recent_pushes.clear()
        await super().async_shutdown()
