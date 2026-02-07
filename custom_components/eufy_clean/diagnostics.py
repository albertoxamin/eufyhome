"""Diagnostics support for Eufy Clean."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EufyCleanDataUpdateCoordinator

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "access_token",
    "user_center_token",
    "gtoken",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    devices_info = {}
    for device_id, device in coordinator.devices.items():
        devices_info[device_id] = {
            "device_name": device.device_name,
            "device_model": device.device_model,
            "api_type": device._api_type,
            "is_novel_api": device.is_novel_api,
            "robovac_data": device._robovac_data,
        }

    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(entry.data, TO_REDACT),
        },
        "devices": devices_info,
        "coordinator_data": coordinator.data,
    }
