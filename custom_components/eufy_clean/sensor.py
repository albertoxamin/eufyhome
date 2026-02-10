"""Support for Eufy Clean sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.controllers import BaseDevice
from .const import DOMAIN, EUFY_CLEAN_DEVICES, MANUFACTURER
from .coordinator import EufyCleanDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EufyCleanSensorEntityDescription(SensorEntityDescription):
    """Describes Eufy Clean sensor entity."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSOR_DESCRIPTIONS: tuple[EufyCleanSensorEntityDescription, ...] = (
    EufyCleanSensorEntityDescription(
        key="work_status",
        translation_key="work_status",
        name="Work Status",
        icon="mdi:robot-vacuum",
        value_fn=lambda data: data.get("work_status", "unknown"),
    ),
    EufyCleanSensorEntityDescription(
        key="work_mode",
        translation_key="work_mode",
        name="Work Mode",
        icon="mdi:cog",
        value_fn=lambda data: data.get("work_mode", "unknown"),
    ),
    EufyCleanSensorEntityDescription(
        key="clean_speed",
        translation_key="clean_speed",
        name="Clean Speed",
        icon="mdi:speedometer",
        value_fn=lambda data: data.get("clean_speed", "standard"),
    ),
    EufyCleanSensorEntityDescription(
        key="error_code",
        translation_key="error_code",
        name="Error",
        icon="mdi:alert-circle",
        value_fn=lambda data: data.get("error_code", "none"),
    ),
    # Consumable life sensors (DPS 168)
    EufyCleanSensorEntityDescription(
        key="rolling_brush_life",
        translation_key="rolling_brush_life",
        name="Rolling Brush Life",
        icon="mdi:brush",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda data: data.get("consumables", {}).get("rolling_brush"),
    ),
    EufyCleanSensorEntityDescription(
        key="side_brush_life",
        translation_key="side_brush_life",
        name="Side Brush Life",
        icon="mdi:brush",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda data: data.get("consumables", {}).get("side_brush"),
    ),
    EufyCleanSensorEntityDescription(
        key="filter_life",
        translation_key="filter_life",
        name="Filter Life",
        icon="mdi:air-filter",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda data: data.get("consumables", {}).get("filter"),
    ),
    EufyCleanSensorEntityDescription(
        key="mop_pad_life",
        translation_key="mop_pad_life",
        name="Mop Pad Life",
        icon="mdi:square-rounded",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda data: data.get("consumables", {}).get("mop_pad"),
    ),
    # Cleaning statistics sensors (DPS 167)
    EufyCleanSensorEntityDescription(
        key="total_cleans",
        translation_key="total_cleans",
        name="Total Cleans",
        icon="mdi:counter",
        value_fn=lambda data: data.get("cleaning_statistics", {}).get("total_cleans"),
    ),
    EufyCleanSensorEntityDescription(
        key="total_area_cleaned",
        translation_key="total_area_cleaned",
        name="Total Area Cleaned",
        icon="mdi:texture-box",
        native_unit_of_measurement="m²",
        value_fn=lambda data: (
            round(data.get("cleaning_statistics", {}).get("total_area", 0) / 10000, 1)
            if data.get("cleaning_statistics", {}).get("total_area")
            else None
        ),
    ),
    EufyCleanSensorEntityDescription(
        key="total_cleaning_time",
        translation_key="total_cleaning_time",
        name="Total Cleaning Time",
        icon="mdi:clock-outline",
        native_unit_of_measurement="h",
        value_fn=lambda data: (
            round(data.get("cleaning_statistics", {}).get("total_time_min", 0) / 60, 1)
            if data.get("cleaning_statistics", {}).get("total_time_min")
            else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eufy Clean sensors from a config entry."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id, device in coordinator.devices.items():
        for description in SENSOR_DESCRIPTIONS:
            entities.append(EufyCleanSensor(coordinator, device, description))

    async_add_entities(entities)


class EufyCleanSensor(CoordinatorEntity[EufyCleanDataUpdateCoordinator], SensorEntity):
    """Representation of a Eufy Clean sensor."""

    _attr_has_entity_name = True
    entity_description: EufyCleanSensorEntityDescription

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
        description: EufyCleanSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self.entity_description = description
        self._attr_unique_id = f"{device.device_id}_{description.key}"

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.coordinator.data and self._device.device_id in self.coordinator.data:
            return self.entity_description.value_fn(
                self.coordinator.data[self._device.device_id]
            )
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
