"""Local RTSP camera entities for EZVIZ devices."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from custom_components.ezviz_cloud.vendor.pyezvizapi.exceptions import (
    HTTPError,
    InvalidHost,
    PyEzvizError,
)
from homeassistant.components import ffmpeg
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import CONF_USE_WALLCLOCK_AS_TIMESTAMPS
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)

from .camera_config import normalize_rtsp_path
from .const import (
    CONF_ENC_KEY,
    CONF_FFMPEG_ARGUMENTS,
    CONF_RTSP_USES_VERIFICATION_CODE,
    CONF_STREAM_MODE,
    DATA_COORDINATOR,
    DEFAULT_CAMERA_USERNAME,
    DEFAULT_FFMPEG_ARGUMENTS,
    DEFAULT_STREAM_MODE,
    DOMAIN,
    OPTIONS_KEY_CAMERAS,
    SERVICE_WAKE_DEVICE,
    STREAM_MODE_DISABLED,
)
from .coordinator import EzvizDataUpdateCoordinator
from .entity import EzvizEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up all camera entities for one cloud account."""
    coordinator: EzvizDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    camera_options: Mapping[str, Mapping[str, Any]] = entry.options.get(OPTIONS_KEY_CAMERAS, {})

    entities: list[EzvizCamera] = []
    for serial in coordinator.data:
        options = camera_options.get(serial, {})
        use_verification_code = bool(options.get(CONF_RTSP_USES_VERIFICATION_CODE, False))
        password = (
            options.get(CONF_PASSWORD, "")
            if use_verification_code
            else options.get(CONF_ENC_KEY, "")
        )
        entities.append(
            EzvizCamera(
                coordinator=coordinator,
                serial=serial,
                camera_username=options.get(CONF_USERNAME, DEFAULT_CAMERA_USERNAME),
                camera_password=password,
                rtsp_path=normalize_rtsp_path(
                    options.get(CONF_FFMPEG_ARGUMENTS, DEFAULT_FFMPEG_ARGUMENTS)
                ),
                stream_mode=options.get(CONF_STREAM_MODE, DEFAULT_STREAM_MODE),
            )
        )

    async_add_entities(entities)
    async_get_current_platform().async_register_entity_service(
        SERVICE_WAKE_DEVICE, None, "perform_wake_device"
    )


class EzvizCamera(EzvizEntity, Camera):
    """EZVIZ camera using a local RTSP source discovered from the cloud."""

    _attr_name: str | None = None

    def __init__(
        self,
        coordinator: EzvizDataUpdateCoordinator,
        serial: str,
        camera_username: str,
        camera_password: str,
        rtsp_path: str,
        stream_mode: str,
    ) -> None:
        """Initialize the camera entity."""
        EzvizEntity.__init__(self, coordinator, serial)
        Camera.__init__(self)
        self.stream_options[CONF_USE_WALLCLOCK_AS_TIMESTAMPS] = True
        self._username = camera_username
        self._password = camera_password
        self._rtsp_path = rtsp_path
        self._stream_mode = stream_mode
        self._attr_unique_id = serial
        self._attr_supported_features = (
            CameraEntityFeature.STREAM if self._build_rtsp() else CameraEntityFeature(0)
        )

    def _build_rtsp(self) -> str | None:
        """Build a credential-escaped local RTSP URL when all parts exist."""
        if self._stream_mode == STREAM_MODE_DISABLED or not self._password:
            return None

        local_ip = self.data.get("local_ip")
        local_port = self.data.get("local_rtsp_port")
        if not isinstance(local_ip, str) or not local_ip.strip():
            return None
        try:
            port = int(local_port)
        except (TypeError, ValueError):
            return None
        if not 0 < port <= 65535:
            return None

        host = local_ip.strip()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        username = quote(self._username, safe="")
        password = quote(self._password, safe="")
        return f"rtsp://{username}:{password}@{host}:{port}{self._rtsp_path}"

    @property
    def is_recording(self) -> bool:
        """Return whether cloud alarm recording is enabled."""
        return bool(self.data.get("alarm_notify"))

    @property
    def motion_detection_enabled(self) -> bool:
        """Return whether motion detection is enabled."""
        return bool(self.data.get("alarm_notify"))

    def enable_motion_detection(self) -> None:
        """Enable motion detection on the device."""
        try:
            self.coordinator.ezviz_client.set_camera_defence(self._serial, 1)
        except InvalidHost as err:
            raise InvalidHost("Error enabling motion detection") from err

    def disable_motion_detection(self) -> None:
        """Disable motion detection on the device."""
        try:
            self.coordinator.ezviz_client.set_camera_defence(self._serial, 0)
        except InvalidHost as err:
            raise InvalidHost("Error disabling motion detection") from err

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a frame from the local RTSP stream."""
        source = self._build_rtsp()
        if source is None:
            return None
        return await ffmpeg.async_get_image(self.hass, source, width=width, height=height)

    async def stream_source(self) -> str | None:
        """Return the local RTSP source without logging its credentials."""
        return self._build_rtsp()

    def perform_wake_device(self) -> None:
        """Wake the camera with a lightweight cloud request."""
        try:
            self.coordinator.ezviz_client.get_detection_sensibility(self._serial)
        except (HTTPError, PyEzvizError) as err:
            raise PyEzvizError("Cannot wake device") from err
