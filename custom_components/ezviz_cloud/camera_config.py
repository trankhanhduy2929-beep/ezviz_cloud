"""Helpers for safe, consistent per-camera configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import (
    CONF_AUTO_CONFIGURED,
    CONF_ENC_KEY,
    CONF_FFMPEG_ARGUMENTS,
    CONF_KEY_STATUS,
    CONF_RTSP_USES_VERIFICATION_CODE,
    CONF_STREAM_MODE,
    DEFAULT_CAMERA_USERNAME,
    DEFAULT_FETCH_MY_KEY,
    DEFAULT_FFMPEG_ARGUMENTS,
    DEFAULT_STREAM_MODE,
    KEY_STATUS_MISSING,
    KEY_STATUS_NOT_REQUIRED,
    KEY_STATUS_READY,
    STREAM_MODE_DISABLED,
    STREAM_MODE_LOCAL_RTSP,
)


def normalize_rtsp_path(value: Any) -> str:
    """Return a safe RTSP path using the substream when invalid."""
    if not isinstance(value, str):
        return DEFAULT_FFMPEG_ARGUMENTS

    path = value.strip()
    if not path:
        return DEFAULT_FFMPEG_ARGUMENTS
    if any(char in path for char in ("\r", "\n", "?", "#")) or "://" in path:
        return DEFAULT_FFMPEG_ARGUMENTS
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def supports_local_rtsp(camera: Mapping[str, Any]) -> bool:
    """Return whether discovery data contains a usable local RTSP endpoint."""
    local_ip = camera.get("local_ip")
    if not isinstance(local_ip, str) or local_ip.strip() in {"", "0.0.0.0", "::"}:
        return False

    port = camera.get("local_rtsp_port")
    try:
        return 0 < int(port) <= 65535
    except (TypeError, ValueError):
        return False


def merge_camera_options(
    cameras: Mapping[str, Mapping[str, Any]],
    encryption_keys: Mapping[str, str],
    existing: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge discovered cameras, fetched keys, and existing user settings."""
    old_options = existing or {}
    merged: dict[str, dict[str, Any]] = {
        serial: dict(options) for serial, options in old_options.items()
    }

    for serial, camera in cameras.items():
        previous = dict(old_options.get(serial, {}))
        fetched_key = encryption_keys.get(serial)
        encryption_key = fetched_key or previous.get(CONF_ENC_KEY, "")
        if encryption_key == DEFAULT_FETCH_MY_KEY:
            encryption_key = ""
        verification_code = previous.get(CONF_PASSWORD, "")
        if verification_code == DEFAULT_FETCH_MY_KEY:
            verification_code = ""
        has_local_rtsp = supports_local_rtsp(camera)
        stream_mode = previous.get(
            CONF_STREAM_MODE,
            DEFAULT_STREAM_MODE if has_local_rtsp else STREAM_MODE_DISABLED,
        )
        if stream_mode not in {STREAM_MODE_LOCAL_RTSP, STREAM_MODE_DISABLED}:
            stream_mode = DEFAULT_STREAM_MODE if has_local_rtsp else STREAM_MODE_DISABLED

        previous.update(
            {
                CONF_USERNAME: previous.get(CONF_USERNAME, DEFAULT_CAMERA_USERNAME),
                CONF_PASSWORD: verification_code,
                CONF_ENC_KEY: encryption_key,
                CONF_RTSP_USES_VERIFICATION_CODE: bool(
                    previous.get(CONF_RTSP_USES_VERIFICATION_CODE, False)
                ),
                CONF_FFMPEG_ARGUMENTS: normalize_rtsp_path(
                    previous.get(CONF_FFMPEG_ARGUMENTS, DEFAULT_FFMPEG_ARGUMENTS)
                ),
                CONF_STREAM_MODE: stream_mode,
                CONF_AUTO_CONFIGURED: True,
                CONF_KEY_STATUS: (
                    KEY_STATUS_READY
                    if encryption_key
                    else KEY_STATUS_MISSING
                    if has_local_rtsp
                    else KEY_STATUS_NOT_REQUIRED
                ),
            }
        )
        merged[serial] = previous

    return merged


def missing_stream_keys(
    cameras: Mapping[str, Mapping[str, Any]],
    options: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """Return camera serials requiring a key for local RTSP."""
    return {
        serial
        for serial, camera in cameras.items()
        if supports_local_rtsp(camera)
        and options.get(serial, {}).get(CONF_STREAM_MODE, DEFAULT_STREAM_MODE)
        != STREAM_MODE_DISABLED
        and not (
            options.get(serial, {}).get(CONF_ENC_KEY)
            or (
                options.get(serial, {}).get(CONF_RTSP_USES_VERIFICATION_CODE)
                and options.get(serial, {}).get(CONF_PASSWORD)
            )
        )
    }
