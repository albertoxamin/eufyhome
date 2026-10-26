"""Support for Eufy Clean time entities."""

from __future__ import annotations

from datetime import time
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.controllers import BaseDevice
from .const import DOMAIN, EUFY_CLEAN_DEVICES, MANUFACTURER
from .coordinator import EufyCleanDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Do Not Disturb start and end times."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[TimeEntity] = []
    for _device_id, device in coordinator.devices.items():
        entities.append(EufyCleanDndTime(coordinator, device, "start"))
        entities.append(EufyCleanDndTime(coordinator, device, "end"))

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


class EufyCleanDndTime(CoordinatorEntity[EufyCleanDataUpdateCoordinator], TimeEntity):
    """Start or end time for Do Not Disturb."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
        endpoint: str,
    ) -> None:
        """Initialize one end of the Do Not Disturb schedule."""
        super().__init__(coordinator)
        self._device = device
        self._endpoint = endpoint
        self._attr_unique_id = f"{device.device_id}_dnd_{endpoint}"
        if endpoint == "start":
            self._attr_name = "Do Not Disturb start"
            self._attr_icon = "mdi:clock-start"
        else:
            self._attr_name = "Do Not Disturb end"
            self._attr_icon = "mdi:clock-end"
        self._attr_device_info = _make_device_info(device)

    def _schedule(self) -> dict[str, Any]:
        """Return the stored Do Not Disturb schedule."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            schedule = self.coordinator.data[self._device.device_id].get("dnd")
            if isinstance(schedule, dict):
                return schedule
        return self._device.get_dnd()

    @property
    def native_value(self) -> time | None:
        """Return the stored start or end time."""
        schedule = self._schedule()
        if self._endpoint == "start":
            return time(
                int(schedule.get("start_hour", 22)),
                int(schedule.get("start_minute", 0)),
            )
        return time(
            int(schedule.get("end_hour", 8)),
            int(schedule.get("end_minute", 0)),
        )

    async def async_set_value(self, value: time) -> None:
        """Write this endpoint and leave the other time and the switch alone."""
        schedule = self._device.get_dnd()
        if self._endpoint == "start":
            await self._device.set_dnd(
                schedule["enabled"],
                value.hour,
                schedule["end_hour"],
                value.minute,
                schedule.get("end_minute", 0),
            )
        else:
            await self._device.set_dnd(
                schedule["enabled"],
                schedule["start_hour"],
                value.hour,
                schedule.get("start_minute", 0),
                value.minute,
            )
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
