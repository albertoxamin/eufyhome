"""Support for Eufy Clean buttons."""

from __future__ import annotations

import logging

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
        if device.is_novel_api:
            entities.append(EufyCleanStopStationButton(coordinator, device))
            entities.append(EufyCleanDryMopButton(coordinator, device))
            entities.append(EufyCleanWashMopButton(coordinator, device))
            entities.append(EufyCleanEmptyDustBinButton(coordinator, device))

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


class EufyCleanStopStationButton(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], ButtonEntity
):
    """Button to stop an active station wash/dry/dust operation."""

    _attr_has_entity_name = True
    _attr_name = "Stop Station"
    _attr_icon = "mdi:stop-circle"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_stop_station"
        self._attr_device_info = _make_device_info(device)

    def _station_status(self) -> dict:
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            return self.coordinator.data[self._device.device_id].get(
                "station_status", {}
            )
        return self._device.get_station_status()

    @property
    def available(self) -> bool:
        """Return True when the station is busy with an operation."""
        station = self._station_status()
        return station.get("connected", False) and station.get("is_busy", False)

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._device.station_stop()
        await self.coordinator.async_request_refresh()


class EufyCleanDryMopButton(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], ButtonEntity
):
    """Button to trigger station mop drying."""

    _attr_has_entity_name = True
    _attr_name = "Dry Mop"
    _attr_icon = "mdi:hair-dryer"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_dry_mop"
        self._attr_device_info = _make_device_info(device)

    @property
    def available(self) -> bool:
        """Return True if station is connected."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            station = self.coordinator.data[self._device.device_id].get(
                "station_status", {}
            )
            return station.get("connected", False)
        return False

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._device.station_dry_mop()


class EufyCleanWashMopButton(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], ButtonEntity
):
    """Button to trigger station mop washing."""

    _attr_has_entity_name = True
    _attr_name = "Wash Mop"
    _attr_icon = "mdi:washing-machine"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_wash_mop"
        self._attr_device_info = _make_device_info(device)

    @property
    def available(self) -> bool:
        """Return True if station is connected."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            station = self.coordinator.data[self._device.device_id].get(
                "station_status", {}
            )
            return station.get("connected", False)
        return False

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._device.station_wash_mop()


class EufyCleanEmptyDustBinButton(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], ButtonEntity
):
    """Button to trigger station dust bin emptying."""

    _attr_has_entity_name = True
    _attr_name = "Empty Dust Bin"
    _attr_icon = "mdi:delete-empty"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_empty_dust_bin"
        self._attr_device_info = _make_device_info(device)

    @property
    def available(self) -> bool:
        """Return True if station is connected."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            station = self.coordinator.data[self._device.device_id].get(
                "station_status", {}
            )
            return station.get("connected", False)
        return False

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._device.station_empty_dust()
