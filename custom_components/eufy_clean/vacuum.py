"""Support for Eufy Clean vacuum robots."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.controllers import BaseDevice
from .const import (
    DOMAIN,
    EUFY_CLEAN_DEVICES,
    EUFY_CLEAN_SPEEDS,
    MANUFACTURER,
)
from .coordinator import EufyCleanDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Map Eufy states to Home Assistant vacuum states
STATE_MAP = {
    "cleaning": "cleaning",
    "docked": "docked",
    "returning": "returning",
    "idle": "idle",
    "error": "error",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eufy Clean vacuum from a config entry."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id, device in coordinator.devices.items():
        entities.append(EufyCleanVacuum(coordinator, device))

    async_add_entities(entities)


class EufyCleanVacuum(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], StateVacuumEntity
):
    """Representation of a Eufy Clean vacuum robot."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        VacuumEntityFeature.BATTERY
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.START
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.LOCATE
    )
    _attr_fan_speed_list = EUFY_CLEAN_SPEEDS

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the Eufy Clean vacuum."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = device.device_id

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    @property
    def state(self) -> str | None:
        """Return the state of the vacuum."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            state = self.coordinator.data[self._device.device_id].get("state", "idle")
            return STATE_MAP.get(state, "idle")
        return self._device.get_state()

    @property
    def battery_level(self) -> int | None:
        """Return the battery level of the vacuum."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            return self.coordinator.data[self._device.device_id].get("battery_level", 0)
        return self._device.get_battery_level()

    @property
    def fan_speed(self) -> str | None:
        """Return the fan speed of the vacuum."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            return self.coordinator.data[self._device.device_id].get(
                "clean_speed", "standard"
            )
        return self._device.get_clean_speed()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {}

        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            data = self.coordinator.data[self._device.device_id]
            attrs = {
                "work_status": data.get("work_status", ""),
                "work_mode": data.get("work_mode", ""),
                "error_code": data.get("error_code", "none"),
                "is_charging": data.get("is_charging", False),
                "is_docked": data.get("is_docked", False),
            }
        else:
            attrs = {
                "work_status": self._device.get_work_status(),
                "work_mode": self._device.get_work_mode(),
                "error_code": self._device.get_error_code(),
                "is_charging": self._device.is_charging(),
                "is_docked": self._device.is_docked(),
            }

        # Add rooms if available
        rooms = self._device.get_rooms()
        if rooms:
            attrs["rooms"] = rooms

        # Add info about room cleaning service
        if self._device.is_novel_api:
            attrs["supports_room_cleaning"] = True
            attrs["room_cleaning_service"] = "eufy_clean.clean_rooms"

        return attrs

    async def async_start(self) -> None:
        """Start cleaning."""
        await self._device.start()
        await self.coordinator.async_request_refresh()

    async def async_pause(self) -> None:
        """Pause cleaning."""
        await self._device.pause()
        await self.coordinator.async_request_refresh()

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop cleaning."""
        await self._device.stop()
        await self.coordinator.async_request_refresh()

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return to base."""
        await self._device.return_to_base()
        await self.coordinator.async_request_refresh()

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set fan speed."""
        await self._device.set_fan_speed(fan_speed)
        await self.coordinator.async_request_refresh()

    async def async_locate(self, **kwargs: Any) -> None:
        """Locate the vacuum."""
        await self._device.locate()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
