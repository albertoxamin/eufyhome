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
            entities.append(EufyRoomSelect(coordinator, device))

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


class EufyRoomSelect(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], SelectEntity
):
    """Select a room segment to clean."""

    _attr_has_entity_name = True
    _attr_name = "Clean Room"
    _attr_icon = "mdi:floor-plan"
    _attr_translation_key = "clean_room"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the room select entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_clean_room"
        self._room_options: dict[str, int] = {}
        self._device_update_callback = self._handle_device_update

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )
        self._refresh_room_options()

    async def async_added_to_hass(self) -> None:
        """Subscribe to live map/room updates from the device."""
        await super().async_added_to_hass()
        self._device.add_update_callback(self._device_update_callback)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from device updates."""
        await super().async_will_remove_from_hass()
        self._device.remove_update_callback(self._device_update_callback)

    def _handle_device_update(self) -> None:
        """Refresh options when room names arrive from the map stream."""
        if self._refresh_room_options():
            self.schedule_update_ha_state(force_refresh=True)

    def _get_rooms(self) -> list[dict[str, Any]]:
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            rooms = self.coordinator.data[self._device.device_id].get("rooms")
            if rooms:
                return rooms
        return self._device.get_rooms()

    def _refresh_room_options(self) -> bool:
        rooms = self._get_rooms()
        options: dict[str, int] = {}
        for room in rooms:
            room_id = room.get("id")
            if room_id is None:
                continue
            name = room.get("name") or f"Room {room_id}"
            options[f"{name} ({room_id})"] = int(room_id)
        changed = options != self._room_options
        self._room_options = options
        self._attr_options = list(options.keys()) or ["No rooms discovered"]
        return changed

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        if not self._room_options:
            return "No rooms discovered"
        return self._attr_options[0]

    async def async_select_option(self, option: str) -> None:
        """Start cleaning the selected room segment."""
        room_id = self._room_options.get(option)
        if room_id is None:
            _LOGGER.warning("Unknown room option selected: %s", option)
            return
        await self._device.clean_rooms([room_id])
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._refresh_room_options()
        self.async_write_ha_state()
