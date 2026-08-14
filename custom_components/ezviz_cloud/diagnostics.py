"""Redacted diagnostics for EZVIZ Cloud."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import EzvizDataUpdateCoordinator

_REDACT_KEYS = {
    "serial",
    "deviceSerial",
    "deviceSerials",
    "fullSerial",
    "last_alarm_pic",
    "wan_ip",
    "wanIp",
    "local_ip",
    "netIp",
    "ssid",
    "wifi_ssid",
    "mac",
    "mac_address",
    "encryptPwd",
    "encrypted_pwd_hash",
    "encryptkey",
    "encryptKey",
    "session_id",
    "rf_session_id",
    "area_id",
    "user_id",
    "ezviz_account",
    "userName",
    "resourceId",
    "superDeviceSerial",
    "CLOUD",
    "VTM",
    "P2P",
    "KMS",
    "TIME_PLAN",
    "CHANNEL",
    "QOS",
    "VIDEO_QUALITY",
}


def _redact_device_mapping(value: Any) -> list[dict[str, Any]]:
    """Redact device-map keys as well as nested sensitive values."""
    if not isinstance(value, Mapping):
        return []
    return [
        {
            "device": index,
            "data": async_redact_data(dict(item), _REDACT_KEYS),
        }
        for index, item in enumerate(value.values())
        if isinstance(item, Mapping)
    ]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without account identifiers or media URLs."""
    coordinator: EzvizDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    page_list = await hass.async_add_executor_job(coordinator.ezviz_client.get_device_infos)
    return {
        "ezviz_coordinator_data": _redact_device_mapping(coordinator.data),
        "ezviz_api_page_list": _redact_device_mapping(page_list),
    }
