"""The Eufy Clean integration."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .api import EufyCleanApi
from .const import DOMAIN
from .coordinator import EufyCleanDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.VACUUM,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SCENE,
    Platform.SWITCH,
]

SERVICE_CLEAN_ROOMS = "clean_rooms"
ATTR_ROOM_IDS = "room_ids"
ATTR_CLEAN_TIMES = "clean_times"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eufy Clean from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    api = EufyCleanApi(username, password)
    coordinator = EufyCleanDataUpdateCoordinator(hass, entry, api)

    if not await coordinator.async_setup():
        raise ConfigEntryNotReady("Failed to connect to Eufy Clean API")

    # Perform initial data fetch
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await async_register_services(hass)

    return True


async def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services."""

    async def handle_clean_rooms(call: ServiceCall) -> None:
        """Handle the clean_rooms service call."""
        entity_ids = call.data.get("entity_id", [])
        room_ids_str = call.data.get(ATTR_ROOM_IDS, "[]")
        clean_times = call.data.get(ATTR_CLEAN_TIMES, 1)

        # Parse room_ids - can be a list or a JSON string
        if isinstance(room_ids_str, str):
            try:
                room_ids = json.loads(room_ids_str)
            except json.JSONDecodeError:
                # Try parsing as comma-separated
                room_ids = [
                    int(x.strip()) for x in room_ids_str.split(",") if x.strip()
                ]
        else:
            room_ids = list(room_ids_str)

        if not room_ids:
            _LOGGER.error("No valid room IDs provided")
            return

        # Get the entity registry
        entity_registry = er.async_get(hass)

        # Find the coordinator for each entity
        for entity_id in entity_ids if isinstance(entity_ids, list) else [entity_ids]:
            entity_entry = entity_registry.async_get(entity_id)
            if not entity_entry:
                continue

            # Get coordinator from domain data
            for entry_id, coordinator in hass.data[DOMAIN].items():
                if isinstance(coordinator, EufyCleanDataUpdateCoordinator):
                    # Find the device
                    for device_id, device in coordinator.devices.items():
                        if (
                            entity_entry.unique_id == device_id
                            or entity_entry.unique_id.startswith(device_id)
                        ):
                            await device.clean_rooms(room_ids, clean_times)
                            _LOGGER.info(
                                "Started room cleaning for %s: rooms=%s",
                                entity_id,
                                room_ids,
                            )
                            return

    # Only register if not already registered
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAN_ROOMS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAN_ROOMS,
            handle_clean_rooms,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): vol.Any(str, [str]),
                    vol.Required(ATTR_ROOM_IDS): vol.Any(str, [int]),
                    vol.Optional(ATTR_CLEAN_TIMES, default=1): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=3)
                    ),
                }
            ),
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN].pop(
            entry.entry_id
        )
        await coordinator.async_shutdown()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
