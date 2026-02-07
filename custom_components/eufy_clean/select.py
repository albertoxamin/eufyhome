"""Support for Eufy Clean select entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.controllers import BaseDevice
from .const import DOMAIN, EUFY_CLEAN_DEVICES, MANUFACTURER
from .coordinator import EufyCleanDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Cleaning type options
CLEAN_TYPE_OPTIONS = {
    "sweep_only": "Sweep Only",
    "mop_only": "Mop Only",
    "sweep_and_mop": "Sweep and Mop",
}

# Mop water level options
MOP_LEVEL_OPTIONS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
}

# Clean extent options
CLEAN_EXTENT_OPTIONS = {
    "normal": "Standard",
    "narrow": "Deep Clean",
    "quick": "Quick Clean",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eufy Clean select entities from a config entry."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id, device in coordinator.devices.items():
        # Only add these for devices with novel API (mopping support)
        if device.is_novel_api:
            # Clean type and mop level when API reports support (from DPS) or model fallback
            if device.supports_clean_type:
                entities.append(EufyCleanTypeSelect(coordinator, device))
                entities.append(EufyMopLevelSelect(coordinator, device))
            entities.append(EufyCleanExtentSelect(coordinator, device))

    async_add_entities(entities)


class EufyCleanTypeSelect(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], SelectEntity
):
    """Select entity for cleaning type (sweep, mop, or both)."""

    _attr_has_entity_name = True
    _attr_name = "Clean Type"
    _attr_icon = "mdi:vacuum"
    _attr_options = list(CLEAN_TYPE_OPTIONS.values())

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_clean_type"
        self._current_option = "Sweep and Mop"

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Find the key for the option
        option_key = None
        for key, value in CLEAN_TYPE_OPTIONS.items():
            if value == option:
                option_key = key
                break

        if option_key:
            await self._device.set_clean_type(option_key)
            self._current_option = option
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class EufyMopLevelSelect(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], SelectEntity
):
    """Select entity for mop water level."""

    _attr_has_entity_name = True
    _attr_name = "Mop Water Level"
    _attr_icon = "mdi:water"
    _attr_options = list(MOP_LEVEL_OPTIONS.values())

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_mop_level"
        self._current_option = "Medium"

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        option_key = None
        for key, value in MOP_LEVEL_OPTIONS.items():
            if value == option:
                option_key = key
                break

        if option_key:
            await self._device.set_mop_level(option_key)
            self._current_option = option
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class EufyCleanExtentSelect(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], SelectEntity
):
    """Select entity for cleaning extent/intensity."""

    _attr_has_entity_name = True
    _attr_name = "Clean Intensity"
    _attr_icon = "mdi:speedometer"
    _attr_options = list(CLEAN_EXTENT_OPTIONS.values())

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_clean_extent"
        self._current_option = "Standard"

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        option_key = None
        for key, value in CLEAN_EXTENT_OPTIONS.items():
            if value == option:
                option_key = key
                break

        if option_key:
            await self._device.set_clean_extent(option_key)
            self._current_option = option
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
