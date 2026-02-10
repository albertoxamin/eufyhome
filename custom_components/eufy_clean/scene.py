"""Support for Eufy Clean scene entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.scene import Scene
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
    """Set up Eufy Clean scene entities from a config entry."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id, device in coordinator.devices.items():
        if not device.is_novel_api:
            continue

        device_data = coordinator.data.get(device_id, {}) if coordinator.data else {}
        scenes = device_data.get("scenes", [])

        for scene_info in scenes:
            if scene_info.get("enabled", True):
                entities.append(
                    EufyCleanScene(coordinator, device, scene_info)
                )

    async_add_entities(entities)


class EufyCleanScene(
    CoordinatorEntity[EufyCleanDataUpdateCoordinator], Scene
):
    """Scene entity for a device-configured cleaning scene."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:play-circle"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
        scene_info: dict[str, Any],
    ) -> None:
        """Initialize the scene entity."""
        super().__init__(coordinator)
        self._device = device
        self._scene_id: int = scene_info["scene_id"]
        self._scene_name: str = scene_info["name"]
        self._attr_name = self._scene_name
        self._attr_unique_id = f"{device.device_id}_scene_{self._scene_id}"

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    async def async_activate(self, **kwargs: Any) -> None:
        """Activate the cleaning scene."""
        await self._device.start_scene(self._scene_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        available = False
        if (
            self.coordinator.data
            and self._device.device_id in self.coordinator.data
        ):
            scenes = self.coordinator.data[self._device.device_id].get("scenes", [])
            for scene in scenes:
                if scene.get("scene_id") == self._scene_id and scene.get("enabled", True):
                    available = True
                    new_name = scene.get("name", self._scene_name)
                    if new_name != self._scene_name:
                        self._scene_name = new_name
                        self._attr_name = new_name
                    break

        self._attr_available = available
        self.async_write_ha_state()
