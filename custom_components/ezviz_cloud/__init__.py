"""EZVIZ Cloud integration lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from custom_components.ezviz_cloud.vendor.pyezvizapi.client import EzvizClient
from custom_components.ezviz_cloud.vendor.pyezvizapi.exceptions import (
    EzvizAuthTokenExpired,
    EzvizAuthVerificationCode,
    HTTPError,
    InvalidURL,
    PyEzvizError,
)
from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_TIMEOUT,
    CONF_TYPE,
    CONF_URL,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir

from .auth import close_local_client
from .camera_config import merge_camera_options, missing_stream_keys
from .const import (
    ATTR_TYPE_CAMERA,
    ATTR_TYPE_CLOUD,
    CONF_AUTO_CONFIGURED,
    CONF_ENC_KEY,
    CONF_EZVIZ_ACCOUNT,
    CONF_FFMPEG_ARGUMENTS,
    CONF_KEY_STATUS,
    CONF_MOTION_CLEAR_SECONDS,
    CONF_RF_SESSION_ID,
    CONF_RTSP_USES_VERIFICATION_CODE,
    CONF_SESSION_ID,
    CONF_STREAM_MODE,
    CONF_USER_ID,
    DATA_COORDINATOR,
    DEFAULT_CAMERA_USERNAME,
    DEFAULT_FFMPEG_ARGUMENTS,
    DEFAULT_MOTION_CLEAR_SECONDS,
    DEFAULT_STREAM_MODE,
    DEFAULT_TIMEOUT,
    DOMAIN,
    ISSUE_CAMERA_KEYS_MISSING,
    KEY_STATUS_MISSING,
    KEY_STATUS_READY,
    MQTT_HANDLER,
    OPTIONS_KEY_CAMERAS,
)
from .coordinator import EzvizDataUpdateCoordinator
from .mqtt import EzvizMqttHandler
from .views import ImageProxyView

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.IMAGE,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SIREN,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.UPDATE,
]

TARGET_VERSION = 5


def _sync_missing_key_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: EzvizDataUpdateCoordinator,
) -> None:
    """Create or clear a repair issue for cameras missing RTSP credentials."""
    camera_options = entry.options.get(OPTIONS_KEY_CAMERAS, {}) or {}
    missing = missing_stream_keys(coordinator.data or {}, camera_options)
    issue_id = f"{ISSUE_CAMERA_KEYS_MISSING}_{entry.entry_id}"
    if missing:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_CAMERA_KEYS_MISSING,
            translation_placeholders={"count": str(len(missing))},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def _update_rotated_tokens(
    hass: HomeAssistant,
    entry: ConfigEntry,
    token: Mapping[str, Any],
) -> None:
    """Persist token fields changed by the refresh endpoint."""
    updates: dict[str, Any] = {}
    for entry_key, token_key in (
        (CONF_SESSION_ID, CONF_SESSION_ID),
        (CONF_RF_SESSION_ID, CONF_RF_SESSION_ID),
        (CONF_URL, "api_url"),
    ):
        value = token.get(token_key)
        if value and value != entry.data.get(entry_key):
            updates[entry_key] = value
    if updates:
        hass.config_entries.async_update_entry(entry, data={**entry.data, **updates})


def _sync_discovered_camera_options(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: EzvizDataUpdateCoordinator,
) -> None:
    """Add defaults for newly discovered cameras without replacing user choices."""
    options = dict(entry.options or {})
    merged = merge_camera_options(
        coordinator.data or {},
        {},
        options.get(OPTIONS_KEY_CAMERAS, {}) or {},
    )
    if merged != options.get(OPTIONS_KEY_CAMERAS, {}):
        options[OPTIONS_KEY_CAMERAS] = merged
        hass.config_entries.async_update_entry(entry, options=options)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one EZVIZ Cloud account from saved tokens."""
    hass.data.setdefault(DOMAIN, {})
    if entry.data.get(CONF_TYPE) != ATTR_TYPE_CLOUD:
        return True

    required = (CONF_SESSION_ID, CONF_RF_SESSION_ID, CONF_URL, CONF_USER_ID)
    if not all(key in entry.data for key in required):
        raise ConfigEntryAuthFailed("Missing EZVIZ token fields")

    timeout = entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
    client = EzvizClient(
        token={
            CONF_SESSION_ID: entry.data[CONF_SESSION_ID],
            CONF_RF_SESSION_ID: entry.data[CONF_RF_SESSION_ID],
            "api_url": entry.data[CONF_URL],
            "username": entry.data[CONF_USER_ID],
        },
        timeout=timeout,
    )

    try:
        token = await hass.async_add_executor_job(client.login)
    except (EzvizAuthTokenExpired, EzvizAuthVerificationCode) as err:
        close_local_client(client)
        raise ConfigEntryAuthFailed from err
    except (InvalidURL, HTTPError, PyEzvizError) as err:
        close_local_client(client)
        raise ConfigEntryNotReady("Unable to connect to EZVIZ") from err
    except Exception as err:  # pragma: no cover - defensive boundary
        close_local_client(client)
        raise ConfigEntryNotReady(
            f"Unexpected EZVIZ login failure type: {type(err).__name__}"
        ) from err

    _update_rotated_tokens(hass, entry, token)

    coordinator = EzvizDataUpdateCoordinator(
        hass,
        api=client,
        api_timeout=timeout,
        config_entry=entry,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        close_local_client(client)
        raise

    _sync_discovered_camera_options(hass, entry, coordinator)

    mqtt_handler = EzvizMqttHandler(hass, client, entry.entry_id)
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
        MQTT_HANDLER: mqtt_handler,
    }
    try:
        await hass.async_add_executor_job(mqtt_handler.start)
    except Exception as err:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        close_local_client(client)
        raise ConfigEntryNotReady(f"Unable to start EZVIZ MQTT ({type(err).__name__})") from err

    async def _shutdown(_event: Any) -> None:
        await hass.async_add_executor_job(mqtt_handler.stop)
        await hass.async_add_executor_job(close_local_client, client)

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _shutdown))

    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("_http_view_registered"):
        hass.http.register_view(ImageProxyView(hass))
        domain_data["_http_view_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _sync_missing_key_issue(hass, entry, coordinator)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload platforms, MQTT, and the account HTTP session."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data and (mqtt := data.get(MQTT_HANDLER)):
        await hass.async_add_executor_job(mqtt.stop)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        if data and (coordinator := data.get(DATA_COORDINATOR)):
            await hass.async_add_executor_job(close_local_client, coordinator.ezviz_client)
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate legacy per-camera entries and option shapes to version 5."""
    if entry.version >= TARGET_VERSION:
        return True

    entry_type = entry.data.get(CONF_TYPE)
    if entry_type == ATTR_TYPE_CAMERA:
        return True
    if entry_type != ATTR_TYPE_CLOUD:
        return True

    previous_options = dict(entry.options or {})
    cameras_map = dict(previous_options.get(OPTIONS_KEY_CAMERAS, {}) or {})
    timeout = previous_options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
    motion_clear_seconds = previous_options.get(
        CONF_MOTION_CLEAR_SECONDS,
        DEFAULT_MOTION_CLEAR_SECONDS,
    )

    legacy_cameras = [
        item
        for item in hass.config_entries.async_entries(DOMAIN)
        if item.entry_id != entry.entry_id and item.data.get(CONF_TYPE) == ATTR_TYPE_CAMERA
    ]
    for camera_entry in legacy_cameras:
        serial = camera_entry.unique_id
        if not serial or serial in cameras_map:
            continue
        enc_key = camera_entry.data.get(CONF_ENC_KEY, "")
        if enc_key == "fetch_my_key":
            enc_key = ""
        verification_code = camera_entry.data.get(CONF_PASSWORD, "")
        if verification_code == "fetch_my_key":
            verification_code = ""
        uses_verification_code = camera_entry.data.get(CONF_RTSP_USES_VERIFICATION_CODE, False)
        has_key = bool(verification_code if uses_verification_code else enc_key)
        cameras_map[serial] = {
            CONF_USERNAME: camera_entry.data.get(CONF_USERNAME, DEFAULT_CAMERA_USERNAME),
            CONF_PASSWORD: verification_code,
            CONF_ENC_KEY: enc_key,
            CONF_RTSP_USES_VERIFICATION_CODE: uses_verification_code,
            CONF_FFMPEG_ARGUMENTS: camera_entry.options.get(
                CONF_FFMPEG_ARGUMENTS, DEFAULT_FFMPEG_ARGUMENTS
            ),
            CONF_STREAM_MODE: DEFAULT_STREAM_MODE,
            CONF_AUTO_CONFIGURED: False,
            CONF_KEY_STATUS: KEY_STATUS_READY if has_key else KEY_STATUS_MISSING,
        }

    data = dict(entry.data)
    data.setdefault(CONF_EZVIZ_ACCOUNT, entry.title)
    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options={
            CONF_TIMEOUT: timeout,
            CONF_MOTION_CLEAR_SECONDS: motion_clear_seconds,
            OPTIONS_KEY_CAMERAS: cameras_map,
        },
        version=TARGET_VERSION,
        minor_version=entry.minor_version,
    )

    victims = [
        item
        for item in hass.config_entries.async_entries(DOMAIN)
        if item.entry_id != entry.entry_id
        and item.version < TARGET_VERSION
        and (item.source == SOURCE_IGNORE or item.data.get(CONF_TYPE) == ATTR_TYPE_CAMERA)
    ]
    for victim in victims:
        try:
            await hass.config_entries.async_remove(victim.entry_id)
        except Exception as err:  # pragma: no cover - best-effort cleanup
            _LOGGER.warning(
                "Unable to remove a legacy EZVIZ entry (%s)",
                type(err).__name__,
            )

    _LOGGER.info("Migrated an EZVIZ Cloud entry to version %d", TARGET_VERSION)
    return True
