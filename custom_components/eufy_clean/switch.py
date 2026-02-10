"""Support for Eufy Clean switch entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    """Set up Eufy Clean switch entities from a config entry."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = []
    for device_id, device in coordinator.devices.items():
        entities.append(EufyCleanDndSwitch(coordinator, device))
        if device.is_novel_api:
            entities.append(EufyCleanBoostIqSwitch(coordinator, device))

    async_add_entities(entities)


def _make_device_info(device: BaseDevice) -> DeviceInfo:
    """Build DeviceInfo for a device."""
    model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)
    return DeviceInfo(
        identifiers={(DOMAIN, device.device_id)},
        name=device.device_name or f"Eufy {model_name}",
        manufacturer=MANUFACTURER,
        model=model_name,
        sw_version=device.device_model,
    )


class EufyCleanDndSwitch(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], SwitchEntity
):
    """Switch entity for Do Not Disturb mode."""

    _attr_has_entity_name = True
    _attr_name = "Do Not Disturb"
    _attr_icon = "mdi:moon-waning-crescent"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_dnd"
        self._attr_device_info = _make_device_info(device)

    @property
    def is_on(self) -> bool | None:
        """Return true if DND is enabled."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            dnd = self.coordinator.data[self._device.device_id].get("dnd", {})
            return dnd.get("enabled", False)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return DND schedule as attributes."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            dnd = self.coordinator.data[self._device.device_id].get("dnd", {})
            return {
                "start_hour": dnd.get("start_hour", 22),
                "end_hour": dnd.get("end_hour", 8),
            }
        return {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on DND."""
        dnd = self._device.get_dnd()
        await self._device.set_dnd(True, dnd["start_hour"], dnd["end_hour"])
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off DND."""
        dnd = self._device.get_dnd()
        await self._device.set_dnd(False, dnd["start_hour"], dnd["end_hour"])
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class EufyCleanBoostIqSwitch(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], SwitchEntity
):
    """Switch entity for BoostIQ (auto suction boost on carpet)."""

    _attr_has_entity_name = True
    _attr_name = "BoostIQ"
    _attr_icon = "mdi:rocket-launch"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_boost_iq"
        self._attr_device_info = _make_device_info(device)

    @property
    def is_on(self) -> bool | None:
        """Return true if BoostIQ is enabled."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            return self.coordinator.data[self._device.device_id].get("boost_iq", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on BoostIQ."""
        await self._device.set_boost_iq(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off BoostIQ."""
        await self._device.set_boost_iq(False)
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
