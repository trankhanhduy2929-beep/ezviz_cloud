"""Normalize low-latency EZVIZ MQTT alarm push messages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any
from urllib.parse import urlsplit

from .const import (
    DEFAULT_MOTION_CLEAR_SECONDS,
    MAX_MOTION_CLEAR_SECONDS,
    MIN_MOTION_CLEAR_SECONDS,
)

MOTION_ACTIVE_SECONDS = float(DEFAULT_MOTION_CLEAR_SECONDS)
PUSH_OVERLAY_SECONDS = 120.0
PUSH_MOTION_CLEAR_GRACE_SECONDS = 1.0

_IMAGE_FIELDS = (
    "image",
    "default_pic_url",
    "media_url_alt1",
    "media_url_alt2",
)


@dataclass(frozen=True, slots=True)
class EzvizPushUpdate:
    """Normalized fields from one EZVIZ alarm push."""

    event_id: str | None
    values: dict[str, Any]


def normalize_motion_clear_seconds(value: Any) -> float:
    """Return a safe motion auto-off duration from config entry options."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return MOTION_ACTIVE_SECONDS
    if not isfinite(seconds):
        return MOTION_ACTIVE_SECONDS
    return min(
        max(seconds, float(MIN_MOTION_CLEAR_SECONDS)),
        float(MAX_MOTION_CLEAR_SECONDS),
    )


def _clean_string(value: Any) -> str | None:
    """Return a non-empty string value."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _remote_image_url(value: Any) -> str | None:
    """Return a usable HTTP(S) image URL from a push field."""
    candidate = _clean_string(value)
    if candidate is None:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return candidate


def _first_image_url(event: Mapping[str, Any], ext: Mapping[str, Any]) -> str | None:
    """Prefer the final image, then use earlier preview URLs from the same push."""
    for candidate in (
        event.get("pic"),
        ext.get("image"),
        *(ext.get(field) for field in _IMAGE_FIELDS[1:]),
        event.get("defaultPic"),
    ):
        if image_url := _remote_image_url(candidate):
            return image_url
    return None


def normalize_push_event(event: Mapping[str, Any]) -> EzvizPushUpdate | None:
    """Convert one MQTT alarm push into coordinator fields without waiting for an image."""
    ext = event.get("ext")
    if not isinstance(ext, Mapping):
        return None

    image_url = _first_image_url(event, ext)
    alert_name = _clean_string(event.get("alert")) or _clean_string(event.get("title"))
    alert_type = ext.get("alert_type_code")
    if alert_type in (None, ""):
        alert_type = event.get("subType")

    if alert_name is None and alert_type in (None, "") and image_url is None:
        return None

    values: dict[str, Any] = {
        "Motion_Trigger": True,
        "Seconds_Last_Trigger": 0.0,
    }

    event_time = _clean_string(ext.get("time")) or _clean_string(event.get("time"))
    if event_time is not None:
        values["last_alarm_time"] = event_time
    if alert_type not in (None, ""):
        values["last_alarm_type_code"] = alert_type
    if alert_name is not None:
        values["last_alarm_type_name"] = alert_name
    if image_url is not None:
        values["last_alarm_pic"] = image_url

    raw_event_id = ext.get("msgId") or event.get("msgId")
    event_id = str(raw_event_id) if raw_event_id not in (None, "") else None
    return EzvizPushUpdate(event_id=event_id, values=values)
