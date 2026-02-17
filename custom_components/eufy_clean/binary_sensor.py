"""Support for Eufy Clean binary sensors."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up Eufy Clean binary sensors from a config entry."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id, device in coordinator.devices.items():
        entities.append(EufyCleanChargingBinarySensor(coordinator, device))
        entities.append(EufyCleanDockedBinarySensor(coordinator, device))

    async_add_entities(entities)


class EufyCleanChargingBinarySensor(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor for charging status."""

    _attr_has_entity_name = True
    _attr_name = "Charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_charging"

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if charging."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            return self.coordinator.data[self._device.device_id].get(
                "is_charging", False
            )
        return self._device.is_charging()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class EufyCleanDockedBinarySensor(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor for docked status."""

    _attr_has_entity_name = True
    _attr_name = "Docked"
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_docked"

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if docked."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            return self.coordinator.data[self._device.device_id].get("is_docked", False)
        return self._device.is_docked()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
