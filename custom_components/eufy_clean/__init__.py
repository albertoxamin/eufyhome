"""The Eufy Clean integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .api import EufyCleanApi
from .config_flow import async_register_map_preview
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
SERVICE_CLEAN_SQUARE = "clean_square"
SERVICE_CLEAN_AREA = "clean_area"
SERVICE_MAP_POINT = "map_point"
SERVICE_SET_ZONES = "set_zones"
SERVICE_START_SCENE = "start_scene"
SERVICE_STATION = "station"
SERVICE_REMOTE = "remote_move"
_CARD_KEY = "map_card"
ATTR_ROOM_IDS = "room_ids"
ATTR_CLEAN_TIMES = "clean_times"
ATTR_SQUARE_X = "x"
ATTR_SQUARE_Y = "y"
ATTR_SQUARE_SIZE = "size"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eufy Clean from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    async_register_map_preview(hass)
    await async_register_map_card(hass)

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    api = EufyCleanApi(username, password)
    coordinator = EufyCleanDataUpdateCoordinator(hass, entry, api)

    if not await coordinator.async_setup():
        raise ConfigEntryNotReady("Failed to connect to Eufy Clean API")

    # Perform initial data fetch
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await async_register_services(hass)

    return True


def _devices_for_call(hass: HomeAssistant, call: ServiceCall) -> list[Any]:
    """Devices whose vacuum or map entity is in the service call."""
    entity_ids = call.data.get("entity_id", [])
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]
    registry = er.async_get(hass)
    found: list[Any] = []
    for entity_id in entity_ids:
        entity_entry = registry.async_get(entity_id)
        if not entity_entry or not entity_entry.unique_id:
            continue
        for coordinator in hass.data[DOMAIN].values():
            if not isinstance(coordinator, EufyCleanDataUpdateCoordinator):
                continue
            for device_id, device in coordinator.devices.items():
                if (
                    entity_entry.unique_id == device_id
                    or entity_entry.unique_id.startswith(device_id)
                ):
                    found.append(device)
                    break
    return found


async def async_register_map_card(hass: HomeAssistant) -> None:
    """Serve the map card and load it on every dashboard."""
    if hass.data.get(DOMAIN, {}).get(_CARD_KEY):
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    frontend = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/eufy_clean/eufy-clean-map-card.js",
                str(frontend / "eufy-clean-map-card.js"),
                False,
            ),
            StaticPathConfig(
                "/eufy_clean/station.webp",
                str(frontend / "station.webp"),
                False,
            ),
        ]
    )
    add_extra_js_url(hass, "/eufy_clean/eufy-clean-map-card.js?v=3")
    hass.data.setdefault(DOMAIN, {})[_CARD_KEY] = True


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
                            await device.clean_rooms(
                                room_ids,
                                clean_times,
                                call.data.get("suction"),
                                call.data.get("mop"),
                                call.data.get("kind"),
                            )
                            _LOGGER.info(
                                "Started room cleaning for %s: rooms=%s",
                                entity_id,
                                room_ids,
                            )
                            return

    async def handle_clean_square(call: ServiceCall) -> None:
        """Handle the clean_square service call."""
        entity_ids = call.data.get("entity_id", [])
        x = int(call.data[ATTR_SQUARE_X])
        y = int(call.data[ATTR_SQUARE_Y])
        size = int(call.data[ATTR_SQUARE_SIZE])
        clean_times = call.data.get(ATTR_CLEAN_TIMES, 1)
        entity_registry = er.async_get(hass)

        for entity_id in entity_ids if isinstance(entity_ids, list) else [entity_ids]:
            entity_entry = entity_registry.async_get(entity_id)
            if not entity_entry:
                continue
            for coordinator in hass.data[DOMAIN].values():
                if not isinstance(coordinator, EufyCleanDataUpdateCoordinator):
                    continue
                for device_id, device in coordinator.devices.items():
                    if (
                        entity_entry.unique_id == device_id
                        or entity_entry.unique_id.startswith(device_id)
                    ):
                        await device.clean_square(x, y, size, clean_times)
                        _LOGGER.info(
                            "Started square clean for %s: centre=(%s,%s) side=%s",
                            entity_id,
                            x,
                            y,
                            size,
                        )
                        return

    async def handle_map_point(call: ServiceCall) -> dict[str, Any]:
        """Return the room and zone under a picture point."""
        devices = _devices_for_call(hass, call)
        if not devices:
            return {"room_id": 0, "room_name": "", "zone_kind": "", "zone_index": -1}
        return devices[0].map_point(float(call.data["x"]), float(call.data["y"]))

    async def handle_clean_area(call: ServiceCall) -> None:
        """Clean the rectangle drawn on the picture."""
        devices = _devices_for_call(hass, call)
        if not devices:
            return
        await devices[0].clean_area(
            float(call.data["x1"]),
            float(call.data["y1"]),
            float(call.data["x2"]),
            float(call.data["y2"]),
            call.data.get(ATTR_CLEAN_TIMES, 1),
            call.data.get("suction"),
            call.data.get("mop"),
            call.data.get("kind"),
        )

    async def handle_set_zones(call: ServiceCall) -> None:
        """Replace no-go and no-mop zones from picture rectangles."""
        devices = _devices_for_call(hass, call)
        if not devices:
            return
        await devices[0].set_zones(
            list(call.data.get("nogo") or []),
            list(call.data.get("nomop") or []),
        )

    async def handle_start_scene(call: ServiceCall) -> None:
        """Start one scenario stored on the robot."""
        devices = _devices_for_call(hass, call)
        if devices:
            await devices[0].start_scene(int(call.data["scene_id"]))

    async def handle_station(call: ServiceCall) -> None:
        """Run one station control."""
        devices = _devices_for_call(hass, call)
        if not devices:
            return
        action = call.data["action"]
        device = devices[0]
        if action == "wash":
            await device.station_wash_mop()
        elif action == "dry":
            await device.station_dry_mop()
        elif action == "empty":
            await device.station_empty_dust()
        else:
            await device.station_stop()

    async def handle_remote(call: ServiceCall) -> None:
        """Drive the robot one direction, or brake."""
        devices = _devices_for_call(hass, call)
        if devices:
            await devices[0].remote_move(call.data["direction"])

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
                    vol.Optional("suction"): [
                        vol.In(("quiet", "standard", "turbo", "max"))
                    ],
                    vol.Optional("mop"): [vol.In(("low", "medium", "high"))],
                    vol.Optional("kind"): [vol.In(("vacuum", "mop", "both"))],
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CLEAN_SQUARE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAN_SQUARE,
            handle_clean_square,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): vol.Any(str, [str]),
                    vol.Required(ATTR_SQUARE_X): vol.All(
                        vol.Coerce(int), vol.Range(min=-20000, max=20000)
                    ),
                    vol.Required(ATTR_SQUARE_Y): vol.All(
                        vol.Coerce(int), vol.Range(min=-20000, max=20000)
                    ),
                    vol.Required(ATTR_SQUARE_SIZE): vol.All(
                        vol.Coerce(int), vol.Range(min=50, max=2000)
                    ),
                    vol.Optional(ATTR_CLEAN_TIMES, default=1): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=3)
                    ),
                }
            ),
        )

    _unit = vol.All(vol.Coerce(float), vol.Range(min=0, max=1))
    # A zone can sit slightly outside the cropped picture. Keep that corner.
    _edge = vol.All(vol.Coerce(float), vol.Range(min=-5, max=5))
    _rect = vol.All([_edge], vol.Length(min=4, max=4))

    if not hass.services.has_service(DOMAIN, SERVICE_MAP_POINT):
        hass.services.async_register(
            DOMAIN,
            SERVICE_MAP_POINT,
            handle_map_point,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): vol.Any(str, [str]),
                    vol.Required("x"): _unit,
                    vol.Required("y"): _unit,
                }
            ),
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CLEAN_AREA):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAN_AREA,
            handle_clean_area,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): vol.Any(str, [str]),
                    vol.Required("x1"): _unit,
                    vol.Required("y1"): _unit,
                    vol.Required("x2"): _unit,
                    vol.Required("y2"): _unit,
                    vol.Optional(ATTR_CLEAN_TIMES, default=1): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=3)
                    ),
                    vol.Optional("suction"): vol.In(
                        ("quiet", "standard", "turbo", "max")
                    ),
                    vol.Optional("mop"): vol.In(("low", "medium", "high")),
                    vol.Optional("kind"): vol.In(("vacuum", "mop", "both")),
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_ZONES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_ZONES,
            handle_set_zones,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): vol.Any(str, [str]),
                    vol.Optional("nogo", default=[]): [_rect],
                    vol.Optional("nomop", default=[]): [_rect],
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_START_SCENE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_START_SCENE,
            handle_start_scene,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): vol.Any(str, [str]),
                    vol.Required("scene_id"): vol.Coerce(int),
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_STATION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_STATION,
            handle_station,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): vol.Any(str, [str]),
                    vol.Required("action"): vol.In(("wash", "dry", "empty", "stop")),
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REMOTE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REMOTE,
            handle_remote,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): vol.Any(str, [str]),
                    vol.Required("direction"): vol.In(
                        ("forward", "back", "left", "right", "brake")
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


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply saved map-look options without reconnecting."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    apply = getattr(coordinator, "apply_map_options", None)
    if apply is not None:
        apply()


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
