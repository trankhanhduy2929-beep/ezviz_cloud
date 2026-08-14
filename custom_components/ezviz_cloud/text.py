"""Editable EZVIZ camera name entities."""

from __future__ import annotations

from pyezvizapi.exceptions import HTTPError, PyEzvizError

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import EzvizDataUpdateCoordinator
from .entity import EzvizEntity

CAMERA_NAME_TEXT = TextEntityDescription(
    key="camera_name",
    translation_key="camera_name",
    mode=TextMode.TEXT,
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up camera-name entities without exposing encryption keys as states."""
    coordinator: EzvizDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(EzvizCameraNameText(coordinator, serial) for serial in coordinator.data)


class EzvizCameraNameText(EzvizEntity, TextEntity):
    """Text entity allowing the camera to be renamed through EZVIZ."""

    _attr_mode = TextMode.TEXT

    def __init__(
        self,
        coordinator: EzvizDataUpdateCoordinator,
        serial: str,
    ) -> None:
        """Initialize the camera name entity."""
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_{CAMERA_NAME_TEXT.key}"
        self.entity_description = CAMERA_NAME_TEXT
        self._attr_native_value = self._camera_name

    async def async_set_value(self, value: str) -> None:
        """Rename the camera through the cloud API."""
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.ezviz_client.update_device_name,
                self._serial,
                value,
            )
        except (HTTPError, PyEzvizError) as err:
            raise HomeAssistantError("Cannot rename the EZVIZ camera") from err

        self._attr_native_value = value
        self.async_write_ha_state()
