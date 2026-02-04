"""Support for Eufy Clean buttons."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.controllers import BaseDevice
from .const import DOMAIN, EUFY_CLEAN_DEVICES, MANUFACTURER
from .coordinator import EufyCleanDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eufy Clean buttons from a config entry."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id, device in coordinator.devices.items():
        entities.append(EufyCleanLocateButton(coordinator, device))

    async_add_entities(entities)


class EufyCleanLocateButton(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], ButtonEntity
):
    """Button to locate the vacuum."""

    _attr_has_entity_name = True
    _attr_name = "Locate"
    _attr_icon = "mdi:map-marker"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_locate"
        
        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._device.locate()
