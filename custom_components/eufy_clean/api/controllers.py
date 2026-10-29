"""Device controllers for Eufy Clean."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from ..const import (
    DEFAULT_MAP_ROTATION,
    EUFY_CLEAN_ERROR_CODES,
    EUFY_CLEAN_GET_STATE,
    EUFY_CLEAN_SPEEDS,
    EUFY_CLEAN_SUPPORTS_CLEAN_TYPE,
    LEGACY_DPS_MAP,
    NOVEL_DPS_MAP,
)
from .map_commands import build_map_get_all_command, build_set_zones_command
from .map_stream import MapStreamHandler, render_settings_from_options
from .proto_utils import (
    CLEAN_EXTENT_NARROW,
    CLEAN_EXTENT_NORMAL,
    CLEAN_EXTENT_QUICK,
    CLEAN_TYPE_MOP_ONLY,
    CLEAN_TYPE_SWEEP_AND_MOP,
    CLEAN_TYPE_SWEEP_ONLY,
    CONTROL_PAUSE_TASK,
    CONTROL_START_AUTO_CLEAN,
    CONTROL_START_GOHOME,
    CONTROL_STOP_TASK,
    MOP_LEVEL_HIGH,
    MOP_LEVEL_LOW,
    MOP_LEVEL_MEDIUM,
    decode_clean_param,
    decode_clean_speed,
    decode_cleaning_statistics,
    decode_consumables,
    decode_dnd,
    decode_error_code,
    explain_fault_code,
    decode_scene_list,
    decode_station_status,
    decode_work_status,
    enrich_station_status,
    encode_clean_param,
    encode_control_command,
    encode_dnd,
    encode_room_clean_command,
    encode_rooms_custom_command,
    encode_scene_clean_command,
    encode_square_clean_command,
    encode_zone_clean_command,
    encode_station_auto_cfg,
    encode_station_manual_cmd,
    is_base64_encoded,
    parse_rooms_from_map_dps,
)

_LOGGER = logging.getLogger(__name__)


def _named_fault(code: int, fallback: str) -> str:
    """Prefer the short legacy phrase, then the app enum phrase."""
    return EUFY_CLEAN_ERROR_CODES.get(code) or explain_fault_code(code) or fallback

# Work statuses where the robot is moving on the map (cleaning, returning, etc.)
_ACTIVE_MAP_STATUSES = frozenset(
    {"cleaning", "go_home", "cruising", "remote_ctrl", "fast_mapping"}
)

class BaseDevice:
    """Base class for Eufy Clean devices."""

    def __init__(self, device_config: dict[str, Any]) -> None:
        """Initialize the device."""
        self._device_id = device_config.get("device_id", "")
        self._device_model = device_config.get("device_model", "")
        self._device_name = device_config.get("device_name", "")
        self._api_type = device_config.get("api_type", "legacy")
        self._dps = device_config.get("dps", {})
        self._robovac_data: dict[str, Any] = {}
        self._novel_api = self._api_type == "novel"
        self._dps_map = NOVEL_DPS_MAP if self._novel_api else LEGACY_DPS_MAP
        self._update_callbacks: list[Callable[[], None]] = []
        # Captured when a callback is registered from the HA event loop.
        self._update_loop: asyncio.AbstractEventLoop | None = None
        # From API DPS decode when available, else fallback to model set
        self._supports_clean_type: bool = device_config.get(
            "supports_clean_type",
            self._device_model in EUFY_CLEAN_SUPPORTS_CLEAN_TYPE
            if self._novel_api
            else False,
        )

    @property
    def device_id(self) -> str:
        """Return device ID."""
        return self._device_id

    @property
    def device_model(self) -> str:
        """Return device model."""
        return self._device_model

    @property
    def device_name(self) -> str:
        """Return device name."""
        return self._device_name

    @property
    def is_novel_api(self) -> bool:
        """Return True if using novel API."""
        return self._novel_api

    @property
    def supports_clean_type(self) -> bool:
        """Return True if device supports clean type (sweep/mop) selection."""
        return self._supports_clean_type

    def add_update_callback(self, callback: Callable[[], None]) -> None:
        """Add callback for data updates.

        Call this from the event loop. Later notifies from the MQTT thread
        are scheduled back onto that loop.
        """
        if self._update_loop is None:
            try:
                self._update_loop = asyncio.get_running_loop()
            except RuntimeError:
                self._update_loop = None
        self._update_callbacks.append(callback)

    def remove_update_callback(self, callback: Callable[[], None]) -> None:
        """Remove a previously registered data-update callback."""
        if callback in self._update_callbacks:
            self._update_callbacks.remove(callback)

    def _notify_update(self) -> None:
        """Notify callbacks. Safe to call from the MQTT thread."""
        loop = self._update_loop
        if loop is not None:
            try:
                on_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                on_loop = False
            if not on_loop:
                if loop.is_running():
                    try:
                        loop.call_soon_threadsafe(self._notify_update)
                    except RuntimeError:
                        _LOGGER.debug("Event loop closed; dropping device update")
                return
        for callback in list(self._update_callbacks):
            try:
                callback()
            except Exception as err:
                _LOGGER.error("Error in update callback: %s", err)

    def map_data(self, dps: dict[str, Any]) -> None:
        """Map DPS data to robovac data."""
        mapped_values = set(self._dps_map.values())
        for key, value in dps.items():
            matched = False
            for map_key, map_value in self._dps_map.items():
                if map_value == key:
                    self._robovac_data[map_key] = value
                    matched = True
            # Store unmapped DPS keys by raw key so map/camera can use them
            if not matched and key not in mapped_values:
                self._robovac_data[key] = value

        map_dps_key = self._dps_map.get("MAP_DATA")
        if map_dps_key and map_dps_key in dps:
            rooms = parse_rooms_from_map_dps(dps[map_dps_key])
            if rooms:
                self._robovac_data["ROOMS"] = rooms
                _LOGGER.debug("Parsed %d rooms from MAP_DATA DPS", len(rooms))

        _LOGGER.debug("Mapped data: %s", self._robovac_data)
        self._notify_update()

    def get_battery_level(self) -> int:
        """Get battery level."""
        return int(self._robovac_data.get("BATTERY_LEVEL", 0))

    def get_clean_speed(self) -> str:
        """Get current clean speed."""
        speed = self._robovac_data.get("CLEAN_SPEED", "standard")

        if self._novel_api and isinstance(speed, str) and is_base64_encoded(speed):
            return decode_clean_speed(speed)

        if isinstance(speed, int) or (isinstance(speed, str) and len(speed) == 1):
            try:
                speed_index = int(speed)
                if 0 <= speed_index < len(EUFY_CLEAN_SPEEDS):
                    return EUFY_CLEAN_SPEEDS[speed_index]
            except (ValueError, IndexError):
                pass

        if isinstance(speed, str):
            return speed.lower()

        return "standard"

    def get_work_status(self) -> str:
        """Get current work status."""
        return self.get_work_status_details().get("state", "charging")

    def get_work_status_details(self) -> dict[str, Any]:
        """Get decoded work status details from DPS."""
        status = self._robovac_data.get("WORK_STATUS", "")
        if self._novel_api and isinstance(status, str) and is_base64_encoded(status):
            return decode_work_status(status)
        if isinstance(status, str) and status:
            return {"state": status.lower(), "mode": "unknown"}
        return {"state": "charging", "mode": "unknown"}

    def get_work_mode(self) -> str:
        """Get current work mode."""
        mode = self._robovac_data.get("WORK_MODE", "")

        if self._novel_api and isinstance(mode, str) and is_base64_encoded(mode):
            decoded = decode_work_status(mode)
            return decoded.get("mode", "auto")

        if isinstance(mode, str):
            return mode.lower()

        return "auto"

    def get_state(self) -> str:
        """Get vacuum state for Home Assistant."""
        work_status = self.get_work_status()
        work_mode = self.get_work_mode()

        # Map novel API states to HA states
        state_map = {
            "standby": "docked",
            "sleep": "idle",
            "fault": "error",
            "charging": "docked",
            "fast_mapping": "cleaning",
            "cleaning": "cleaning",
            "remote_ctrl": "cleaning",
            "go_home": "returning",
            "cruising": "cleaning",
        }

        state = state_map.get(work_status)
        if not state:
            state = EUFY_CLEAN_GET_STATE.get(work_status)
        if not state:
            state = EUFY_CLEAN_GET_STATE.get(work_mode, "idle")

        return state

    def get_error_code(self) -> str | int:
        """Get current error code."""
        error = self._robovac_data.get("ERROR_CODE", 0)

        if self._novel_api and isinstance(error, str) and is_base64_encoded(error):
            decoded = decode_error_code(error)
            error_text = decoded.get("error_text", "none")

            # Try to get human-readable error
            if decoded.get("errors"):
                return _named_fault(decoded["errors"][0], error_text)
            if decoded.get("warnings"):
                return _named_fault(decoded["warnings"][0], error_text)

            return error_text

        if isinstance(error, int):
            return _named_fault(error, f"unknown_error_{error}")

        return error if error else "none"

    def is_charging(self) -> bool:
        """Check if device is charging."""
        work_status = self.get_work_status()
        return work_status in ("charging", "standby")

    def is_docked(self) -> bool:
        """Check if device is docked."""
        state = self.get_state()
        return state in ("docked", "idle", "charging")

    async def connect(self) -> None:
        """Connect to device."""
        raise NotImplementedError

    async def update(self) -> None:
        """Update device data."""
        raise NotImplementedError

    async def send_command(self, data: dict[str, Any]) -> None:
        """Send command to device."""
        raise NotImplementedError

    async def start(self, clean_times: int = 1, edge_mop: bool | None = None) -> None:
        """Start cleaning. ``clean_times`` 2 is the app's cross pass."""
        if self._novel_api:
            if edge_mop is not None:
                await self.set_edge_mop(edge_mop)
            # Encode control command for novel API using protobuf
            command = encode_control_command(
                CONTROL_START_AUTO_CLEAN, {"clean_times": clean_times}
            )
            await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        else:
            await self.send_command({self._dps_map["WORK_MODE"]: "auto"})
            await self.send_command({self._dps_map["PLAY_PAUSE"]: True})

    async def pause(self) -> None:
        """Pause cleaning."""
        if self._novel_api:
            command = encode_control_command(CONTROL_PAUSE_TASK)
            await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        else:
            await self.send_command({self._dps_map["PLAY_PAUSE"]: False})

    async def stop(self) -> None:
        """Stop cleaning."""
        if self._novel_api:
            command = encode_control_command(CONTROL_STOP_TASK)
            await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        else:
            await self.send_command({self._dps_map["PLAY_PAUSE"]: False})

    async def return_to_base(self) -> None:
        """Return to charging base."""
        if self._novel_api:
            command = encode_control_command(CONTROL_START_GOHOME)
            await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        else:
            await self.send_command({self._dps_map["GO_HOME"]: True})

    async def set_fan_speed(self, speed: str) -> None:
        """Set fan speed."""
        speed = speed.lower()

        if self._novel_api:
            try:
                speed_index = EUFY_CLEAN_SPEEDS.index(speed)
                await self.send_command({self._dps_map["CLEAN_SPEED"]: speed_index})
            except ValueError:
                _LOGGER.error("Invalid speed: %s", speed)
        else:
            await self.send_command({self._dps_map["CLEAN_SPEED"]: speed})

    async def locate(self) -> None:
        """Locate the vacuum."""
        await self.send_command({self._dps_map["FIND_ROBOT"]: True})

    async def set_clean_type(self, clean_type: str) -> None:
        """Set cleaning type (sweep_only, mop_only, sweep_and_mop)."""
        if not self._novel_api:
            _LOGGER.warning("Clean type not supported on legacy devices")
            return

        type_map = {
            "sweep_only": CLEAN_TYPE_SWEEP_ONLY,
            "mop_only": CLEAN_TYPE_MOP_ONLY,
            "sweep_and_mop": CLEAN_TYPE_SWEEP_AND_MOP,
        }

        clean_type_value = type_map.get(clean_type.lower())
        if clean_type_value is not None:
            command = encode_clean_param(clean_type=clean_type_value)
            await self.send_command({self._dps_map["CLEANING_PARAMETERS"]: command})
        else:
            _LOGGER.error("Invalid clean type: %s", clean_type)

    async def set_mop_level(self, level: str) -> None:
        """Set mop water level (low, medium, high)."""
        if not self._novel_api:
            _LOGGER.warning("Mop level not supported on legacy devices")
            return

        level_map = {
            "low": MOP_LEVEL_LOW,
            "medium": MOP_LEVEL_MEDIUM,
            "high": MOP_LEVEL_HIGH,
        }

        mop_level_value = level_map.get(level.lower())
        if mop_level_value is not None:
            command = encode_clean_param(mop_level=mop_level_value)
            await self.send_command({self._dps_map["CLEANING_PARAMETERS"]: command})
        else:
            _LOGGER.error("Invalid mop level: %s", level)

    async def set_edge_mop(self, enabled: bool) -> None:
        """Set deep corner mopping. True hugs the edges on the next clean."""
        if not self._novel_api:
            return
        raw = self._robovac_data.get("CLEANING_PARAMETERS", "")
        decoded = decode_clean_param(raw) if isinstance(raw, str) else None
        level = decoded.get("mop_level") if decoded else None
        command = encode_clean_param(
            mop_level=level,
            corner_clean=1 if enabled else 0,
        )
        await self.send_command({self._dps_map["CLEANING_PARAMETERS"]: command})

    async def set_clean_extent(self, extent: str) -> None:
        """Set cleaning extent/intensity (normal, narrow/deep, quick)."""
        if not self._novel_api:
            _LOGGER.warning("Clean extent not supported on legacy devices")
            return

        extent_map = {
            "normal": CLEAN_EXTENT_NORMAL,
            "narrow": CLEAN_EXTENT_NARROW,
            "deep": CLEAN_EXTENT_NARROW,
            "quick": CLEAN_EXTENT_QUICK,
        }

        extent_value = extent_map.get(extent.lower())
        if extent_value is not None:
            command = encode_clean_param(clean_extent=extent_value)
            await self.send_command({self._dps_map["CLEANING_PARAMETERS"]: command})
        else:
            _LOGGER.error("Invalid clean extent: %s", extent)

    async def clean_rooms(
        self,
        room_ids: list[int],
        clean_times: int | list[int] = 1,
        suction: list[str] | None = None,
        mop: list[str] | None = None,
        kind: list[str] | None = None,
    ) -> None:
        """Start cleaning specific rooms by their IDs.

        When ``suction`` and ``mop`` match ``room_ids``, each room keeps its
        own level and the clean runs in custom mode.
        """
        if not self._novel_api:
            _LOGGER.warning("Room cleaning not supported on legacy devices")
            return

        if not room_ids:
            _LOGGER.error("No room IDs provided")
            return

        customize = bool(suction and mop and len(suction) == len(mop) == len(room_ids))
        if isinstance(clean_times, list):
            per_room = [int(value) for value in clean_times] or [1]
            shared = per_room[0]
        else:
            shared = int(clean_times)
            per_room = []
        map_id, releases = self._floor_ids() if customize else (0, 0)
        if customize:
            rooms = [
                {
                    "id": room_id,
                    "suction": suction[index],
                    "mop": mop[index],
                    "kind": kind[index] if kind and index < len(kind) else "both",
                    "times": per_room[index] if index < len(per_room) else shared,
                }
                for index, room_id in enumerate(room_ids)
            ]
            await self.send_command({"170": encode_rooms_custom_command(rooms, map_id)})
        command = encode_room_clean_command(
            room_ids, shared, map_id, releases, customize
        )
        await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        _LOGGER.info("Started cleaning rooms: %s", room_ids)

    async def clean_square(
        self, x: int, y: int, size: int, clean_times: int = 1
    ) -> None:
        """Start cleaning one square. ``x`` and ``y`` are the centre in centimetres."""
        if not self._novel_api:
            _LOGGER.warning("Zone cleaning not supported on legacy devices")
            return

        handler = getattr(self, "_map_handler", None)
        floor = handler.map_data if handler is not None else None
        command = encode_square_clean_command(
            x,
            y,
            size,
            clean_times,
            map_id=floor.map_id if floor else 0,
            releases=floor.releases if floor else 0,
        )
        await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        _LOGGER.info(
            "Started square clean centre=(%s,%s) side=%s cm", x, y, size
        )

    def _floor_ids(self) -> tuple[int, int]:
        handler = getattr(self, "_map_handler", None)
        floor = handler.map_data if handler is not None else None
        if floor is None:
            return 0, 0
        return floor.map_id, floor.releases

    def map_point(self, nx: float, ny: float) -> dict[str, Any]:
        """Return the room and zone under a point on the rendered picture."""
        handler = getattr(self, "_map_handler", None)
        if handler is None:
            return {"room_id": 0, "room_name": "", "zone_kind": "", "zone_index": -1}
        return handler.picture_hit(nx, ny)

    def get_zone_boxes(self) -> dict[str, list[dict[str, Any]]]:
        """No-go and no-mop boxes in picture coordinates."""
        handler = getattr(self, "_map_handler", None)
        if handler is None:
            return {"nogo": [], "nomop": []}
        return handler.picture_zone_boxes()

    async def clean_area(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        clean_times: int = 1,
        suction: str | None = None,
        mop: str | None = None,
        kind: str | None = None,
    ) -> None:
        """Start cleaning the rectangle drawn on the picture."""
        if not self._novel_api:
            _LOGGER.warning("Zone cleaning not supported on legacy devices")
            return
        handler = getattr(self, "_map_handler", None)
        quad = handler.picture_rect_quad(x1, y1, x2, y2) if handler else None
        if not quad:
            _LOGGER.error("No map frame for area clean")
            return
        clean_type = {"vacuum": "sweep_only", "mop": "mop_only", "both": "sweep_and_mop"}.get(
            kind or ""
        )
        if clean_type:
            await self.set_clean_type(clean_type)
        if suction and kind != "mop":
            await self.set_fan_speed(suction)
        if mop and kind != "vacuum":
            await self.set_mop_level(mop)
        map_id, releases = self._floor_ids()
        command = encode_zone_clean_command(quad, clean_times, map_id, releases)
        await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        _LOGGER.info("Started area clean corners=%s", quad)

    async def set_zones(
        self,
        nogo: list[list[float]],
        nomop: list[list[float]],
    ) -> None:
        """Replace no-go and no-mop zones. Rectangles are picture fractions."""
        if not self._novel_api:
            _LOGGER.warning("Zone editing not supported on legacy devices")
            return
        handler = getattr(self, "_map_handler", None)
        floor = handler.map_data if handler is not None else None
        if handler is None or floor is None or handler.map_frame is None:
            _LOGGER.error("No map frame for zone edit")
            return

        def quads(rects: list[list[float]]) -> list[list[tuple[int, int]]]:
            out = []
            for rect in rects:
                quad = handler.picture_rect_quad(rect[0], rect[1], rect[2], rect[3])
                if quad:
                    out.append(quad)
            return out

        command = build_set_zones_command(
            floor.virtual_walls,
            quads(nogo),
            quads(nomop),
            floor.map_id,
            floor.releases,
        )
        await self.send_command(command)
        _LOGGER.info("Sent zone edit nogo=%s nomop=%s", len(nogo), len(nomop))

    def get_volume(self) -> int:
        """Get current volume level (0-100)."""
        return int(self._robovac_data.get("VOLUME", 0))

    async def set_volume(self, volume: int) -> None:
        """Set volume level (0-100)."""
        volume = max(0, min(100, volume))
        await self.send_command({self._dps_map["VOLUME"]: volume})

    def get_scenes(self) -> list[dict[str, Any]]:
        """Get list of cleaning scenes configured on the device."""
        if not self._novel_api:
            return []
        raw = self._robovac_data.get("SCENE_LIST", "")
        if not raw or not isinstance(raw, str):
            return []
        return decode_scene_list(raw)

    async def remote_move(self, direction: str, start: bool = False) -> None:
        """Drive the robot. The first press also enters manual mode.

        DPS 155 is a RemoteCtrl protobuf. Wheels run after START_RC_CLEAN.
        """
        try:
            value = {"brake": 0, "forward": 1, "back": 2, "left": 3, "right": 4}[direction]
        except KeyError:
            _LOGGER.error("Invalid remote direction: %s", direction)
            return
        if start and direction != "brake":
            await self.send_command(
                {self._dps_map["PLAY_PAUSE"]: encode_control_command(5)}
            )
        await self.send_command({self._dps_map.get("DIRECTION", "155"): value})
        if direction == "brake" and start:
            await self.send_command(
                {self._dps_map["PLAY_PAUSE"]: encode_control_command(16)}
            )

    async def start_scene(self, scene_id: int) -> None:
        """Start a cleaning scene by its ID."""
        if not self._novel_api:
            _LOGGER.warning("Scene clean not supported on legacy devices")
            return
        command = encode_scene_clean_command(scene_id)
        await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        _LOGGER.info("Started scene clean: %s", scene_id)

    def get_dnd(self) -> dict[str, Any]:
        """Get Do Not Disturb status and schedule."""
        raw = self._robovac_data.get("DND", "")
        if not raw or not isinstance(raw, str):
            return {
                "enabled": False,
                "start_hour": 22,
                "start_minute": 0,
                "end_hour": 8,
                "end_minute": 0,
            }
        return decode_dnd(raw)

    async def set_dnd(
        self,
        enabled: bool,
        start_hour: int,
        end_hour: int,
        start_minute: int = 0,
        end_minute: int = 0,
    ) -> None:
        """Set Do Not Disturb status and schedule."""
        command = encode_dnd(
            enabled, start_hour, end_hour, start_minute, end_minute
        )
        await self.send_command({self._dps_map.get("DND", "157"): command})

    def get_boost_iq(self) -> bool:
        """Get BoostIQ status."""
        return bool(self._robovac_data.get("BOOST_IQ", False))

    async def set_boost_iq(self, enabled: bool) -> None:
        """Set BoostIQ on/off."""
        await self.send_command({self._dps_map.get("BOOST_IQ", "159"): enabled})

    def get_cleaning_statistics(self) -> dict[str, Any]:
        """Get cleaning statistics (total cleans, area, time)."""
        raw = self._robovac_data.get("CLEANING_STATISTICS", "")
        if not raw or not isinstance(raw, str):
            return {
                "total_cleans": 0,
                "total_area": 0,
                "total_time_min": 0,
                "total_sessions": 0,
            }
        return decode_cleaning_statistics(raw)

    def get_consumables(self) -> dict[str, Any]:
        """Get consumable/accessory usage hours."""
        raw = self._robovac_data.get("ACCESSORIES_STATUS", "")
        if not raw or not isinstance(raw, str):
            return {
                "rolling_brush": 0,
                "side_brush": 0,
                "filter": 0,
                "mop_pad": 0,
                "other_brush": 0,
                "sensor": 0,
                "runtime_hours": 0,
            }
        return decode_consumables(raw)

    def get_rooms(self) -> list[dict[str, Any]]:
        """Get list of available rooms (vacuum segments)."""
        names = self.get_room_names()
        if names:
            return [
                {"id": room_id, "name": name}
                for room_id, name in sorted(names.items())
            ]
        rooms = self._robovac_data.get("ROOMS", [])
        return rooms if isinstance(rooms, list) else []

    def get_map_image(self) -> bytes | None:
        """Return rendered map PNG from the MQTT biz/ stream, if available."""
        return None

    def has_map_stream(self) -> bool:
        """Return True when a live map stream handler is active."""
        return False

    def get_room_names(self) -> dict[int, str]:
        """Return discovered room id → name mapping, if available."""
        return {}

    def has_room_colors(self) -> bool:
        """Return True when room color mask data is available."""
        return False

    def has_restricted_zones(self) -> bool:
        """Return True when no-go / no-mop zone data is available."""
        return False

    def get_restricted_zone_counts(self) -> dict[str, int]:
        """Return counts of virtual walls and zone polygons."""
        return {
            "virtual_walls": 0,
            "forbidden_zones": 0,
            "ban_mop_zones": 0,
        }

    def has_cleaning_path(self) -> bool:
        """Return True when cleaning path data is available on the map."""
        return False

    def get_robot_pixel(self) -> tuple[int, int] | None:
        """Return the last known robot pixel on the rendered map."""
        return None

    def get_station_status(self) -> dict[str, Any]:
        """Get decoded station status from DPS 173."""
        defaults = {
            "connected": False,
            "state": "idle",
            "collecting_dust": False,
            "clean_water_pct": 0,
            "auto_empty_enabled": False,
            "auto_wash_enabled": False,
            "operation": "idle",
            "operation_label": "Idle",
            "is_busy": False,
        }
        if not self._novel_api:
            return defaults
        raw = self._robovac_data.get("STATION_STATUS", "")
        if not raw or not isinstance(raw, str):
            return defaults
        station = decode_station_status(raw)
        return enrich_station_status(station, self.get_work_status_details())

    def has_station(self) -> bool:
        """Return True if a station is connected."""
        return self.get_station_status().get("connected", False)

    async def station_stop(self) -> None:
        """Stop an active station operation (wash, dry, dust empty, etc.)."""
        await self.stop()

    async def station_dry_mop(self) -> None:
        """Send manual dry mop command to station."""
        command = encode_station_manual_cmd("go_dry")
        await self.send_command({self._dps_map.get("STATION_STATUS", "173"): command})

    async def station_wash_mop(self) -> None:
        """Send manual wash mop command to station."""
        command = encode_station_manual_cmd("go_selfcleaning")
        await self.send_command({self._dps_map.get("STATION_STATUS", "173"): command})

    async def station_empty_dust(self) -> None:
        """Send manual empty dust bin command to station."""
        command = encode_station_manual_cmd("go_collect_dust")
        await self.send_command({self._dps_map.get("STATION_STATUS", "173"): command})

    async def set_station_auto_empty(self, enabled: bool) -> None:
        """Set station auto-empty dust configuration."""
        command = encode_station_auto_cfg(auto_empty=enabled)
        await self.send_command({self._dps_map.get("STATION_STATUS", "173"): command})

    async def set_station_auto_wash(self, enabled: bool) -> None:
        """Set station auto-wash mop configuration."""
        command = encode_station_auto_cfg(auto_wash=enabled)
        await self.send_command({self._dps_map.get("STATION_STATUS", "173"): command})


class CloudDevice(BaseDevice):
    """Cloud-connected Eufy device."""

    def __init__(
        self,
        device_config: dict[str, Any],
        session: aiohttp.ClientSession,
        access_token: str,
        openudid: str,
    ) -> None:
        """Initialize the cloud device."""
        super().__init__(device_config)
        self._session = session
        self._access_token = access_token
        self._openudid = openudid

    async def connect(self) -> None:
        """Connect to device."""
        await self.update()

    async def update(self) -> None:
        """Update device data from cloud."""
        headers = {
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "user-agent": "EufyHome-Android-3.1.3-753",
            "timezone": "Europe/Berlin",
            "category": "Home",
            "token": self._access_token,
            "openudid": self._openudid,
            "clienttype": "2",
            "language": "en",
            "country": "US",
        }

        try:
            async with self._session.get(
                "https://api.eufylife.com/v1/device/v2",
                headers=headers,
            ) as resp:
                result = await resp.json()
                data = result.get("data", result)
                devices = data.get("devices", [])

                for device in devices:
                    if device.get("id") == self._device_id:
                        dps = device.get("dps", {})
                        self.map_data(dps)
                        break
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to update device %s: %s", self._device_id, err)

    async def send_command(self, data: dict[str, Any]) -> None:
        """Send command to cloud device."""
        _LOGGER.debug("Sending cloud command to %s: %s", self._device_id, data)

        # Note: This would need the Tuya Cloud API implementation
        # For now, we'll just log the command
        _LOGGER.info("Command to device %s: %s", self._device_id, data)


class MqttDevice(BaseDevice):
    """MQTT-connected Eufy device."""

    def __init__(
        self,
        device_config: dict[str, Any],
        mqtt_credentials: dict[str, Any],
        openudid: str,
        user_info: dict[str, Any],
        session: aiohttp.ClientSession,
        map_rotation: int = DEFAULT_MAP_ROTATION,
    ) -> None:
        """Initialize the MQTT device."""
        super().__init__(device_config)
        self._mqtt_credentials = mqtt_credentials
        self._openudid = openudid
        self._user_info = user_info
        self._session = session
        self._mqtt_client = None
        self._connected = False
        self._map_handler = MapStreamHandler(rotation=map_rotation)
        self._cert_path: str | None = None
        self._key_path: str | None = None
        self._map_requested = False
        self._empty_room_polls = 0
        self._last_work_status: str | None = None
        self._map_store = None

    def map_data(self, dps: dict[str, Any]) -> None:
        """Map DPS data and merge any room list into the live map handler."""
        super().map_data(dps)
        rooms = self._robovac_data.get("ROOMS", [])
        if rooms:
            names = {
                int(room["id"]): room["name"]
                for room in rooms
                if isinstance(room, dict) and room.get("id")
            }
            if names and self._map_handler._merge_room_params(names):
                self._notify_update()

    def get_map_image(self) -> bytes | None:
        """Return rendered map PNG from the MQTT biz/ stream."""
        return self._map_handler.map_image

    def attach_map_store(self, store) -> None:
        """Remember where to keep the last floor across restarts."""
        self._map_store = store

    def restore_map_snapshot(self, payload: dict[str, Any]) -> bool:
        """Show a previously saved floor before the vacuum answers."""
        return self._map_handler.restore_map_snapshot(payload)

    def _save_map_snapshot(self) -> None:
        store = self._map_store
        if store is None or self._map_handler.map_data is None:
            return
        store.async_delay_save(self._map_handler.export_map_snapshot)

    def _notify_update(self) -> None:
        """Save the floor on the event loop, then notify entities."""
        loop = self._update_loop
        if loop is not None:
            try:
                on_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                on_loop = False
            if on_loop:
                self._save_map_snapshot()
        super()._notify_update()

    def has_map_stream(self) -> bool:
        """Return True when map stream data has been received."""
        return self._map_handler.map_data is not None

    def get_room_names(self) -> dict[int, str]:
        """Return room names from the map stream and MAP_DATA DPS."""
        names = self._map_handler.get_room_names()
        for room in self._robovac_data.get("ROOMS", []):
            if isinstance(room, dict) and room.get("id"):
                names[int(room["id"])] = room.get("name", f"Room {room['id']}")
        return names

    def has_room_colors(self) -> bool:
        """Return True when a room color mask has been received."""
        md = self._map_handler.map_data
        if md and md.room_pixels and md.room_outline_width:
            return True
        return self._map_handler._pending_room_outline is not None

    def has_restricted_zones(self) -> bool:
        """Return True when restricted-zone geometry has been received."""
        return self._map_handler.has_restricted_zones()

    def has_cleaning_path(self) -> bool:
        """Return True when streamed or embedded cleaning path data exists."""
        return self._map_handler.has_cleaning_path()

    def get_robot_pixel(self) -> tuple[int, int] | None:
        """Return the last known robot pixel on the rendered map."""
        return self._map_handler.robot_pixel

    def get_robot_room(self) -> str | None:
        """Return the room name under the robot, or None when it is not in a room."""
        found = self._map_handler.robot_room()
        return None if found is None else found[1]

    def get_robot_room_id(self) -> int | None:
        """Return the room id under the robot, or None when it is not in a room."""
        found = self._map_handler.robot_room()
        return None if found is None else found[0]

    def get_restricted_zone_counts(self) -> dict[str, int]:
        """Return counts of virtual walls and zone polygons."""
        return self._map_handler.restricted_zone_counts()

    def set_map_rotation(self, rotation: int) -> None:
        """Update map image rotation and re-render."""
        self._map_handler.set_rotation(rotation)
        if self._map_handler.map_data is not None:
            self._map_handler._render()
            self._notify_update()

    def apply_render_options(self, options: dict[str, Any]) -> None:
        """Apply saved map-look options and refresh the camera image."""
        if self._map_handler.set_render_options(render_settings_from_options(options)):
            self._notify_update()

    def render_preview(self, options: dict[str, Any]) -> bytes | None:
        """Render the current map with draft options, without saving them."""
        return self._map_handler.render_preview(render_settings_from_options(options))

    async def request_map_download(self) -> None:
        """Trigger P2P map download via DPS 172 MAP_GET_ALL."""
        await self.send_command(build_map_get_all_command())
        self._map_requested = True

    async def connect(self) -> None:
        """Connect to MQTT broker."""
        try:
            # Import paho-mqtt
            import paho.mqtt.client as mqtt_client

            if not self._mqtt_credentials:
                _LOGGER.error("No MQTT credentials available")
                return

            client_id = f"android-{self._mqtt_credentials.get('app_name', 'eufy_home')}-eufy_android_{self._openudid}_{self._mqtt_credentials.get('user_id', '')}"

            self._mqtt_client = mqtt_client.Client(client_id=client_id)

            # Set up TLS with certificate
            cert_pem = self._mqtt_credentials.get("certificate_pem", "")
            private_key = self._mqtt_credentials.get("private_key", "")

            if cert_pem and private_key:
                # Write certs to temp files (in production, use proper cert handling)
                import os
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".pem", delete=False
                ) as cert_file:
                    cert_file.write(cert_pem)
                    self._cert_path = cert_file.name

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".key", delete=False
                ) as key_file:
                    key_file.write(private_key)
                    self._key_path = key_file.name
                    os.chmod(self._key_path, 0o600)

                self._mqtt_client.tls_set(
                    certfile=self._cert_path,
                    keyfile=self._key_path,
                )
                self._mqtt_client.tls_insecure_set(True)

            # Set callbacks
            self._mqtt_client.on_connect = self._on_connect
            self._mqtt_client.on_message = self._on_message
            self._mqtt_client.on_disconnect = self._on_disconnect

            # Connect
            endpoint = self._mqtt_credentials.get("endpoint_addr", "")
            if endpoint:
                self._mqtt_client.connect_async(endpoint, 8883)
                self._mqtt_client.loop_start()
                import asyncio

                for _ in range(20):
                    if self._connected:
                        break
                    await asyncio.sleep(0.5)
                if self._connected and not self._map_requested:
                    await self.request_map_download()

        except Exception as err:
            _LOGGER.error("Failed to connect MQTT: %s", err)

    def _on_connect(self, client, userdata, flags, rc):
        """Handle MQTT connection."""
        if rc == 0:
            _LOGGER.info("Connected to MQTT broker")
            self._connected = True

            # Subscribe to device topics
            topic_res = f"cmd/eufy_home/{self._device_model}/{self._device_id}/res"
            topic_biz = f"biz/eufy_home/{self._device_model}/{self._device_id}/res"
            topic_smart = f"smart/mb/in/{self._device_id}"

            client.subscribe(topic_res)
            client.subscribe(topic_biz)
            client.subscribe(topic_smart)
            _LOGGER.debug("Subscribed to %s, %s, %s", topic_res, topic_biz, topic_smart)
        else:
            _LOGGER.error("MQTT connection failed with code %d", rc)

    def _update_map_tracking(self) -> None:
        """Sync live trail accumulation with the current work status."""
        work_status = self.get_work_status()
        if (
            work_status == "cleaning"
            and self._last_work_status not in (None, "cleaning")
        ):
            self._map_handler.clear_trail_on_new_session()
        self._last_work_status = work_status
        self._map_handler.set_tracking_active(work_status in _ACTIVE_MAP_STATUSES)
        # Coverage stays a solid line. Returning home and trips to a target are dotted.
        self._map_handler.set_path_travel(
            work_status in {"go_home", "cruising", "remote_ctrl", "relocating"}
        )

    def _on_message(self, client, userdata, msg):
        """Handle MQTT message."""
        try:
            topic_biz = f"biz/eufy_home/{self._device_model}/{self._device_id}/res"
            if msg.topic == topic_biz:
                updated = self._map_handler.handle_biz_payload(msg.payload)
                self._update_map_tracking()
                if updated or self._map_handler.map_data is not None:
                    self._notify_update()
                return

            payload = json.loads(msg.payload.decode())
            data = payload.get("payload", {})

            if isinstance(data, str):
                data = json.loads(data)

            dps = data.get("data", {})
            if dps:
                self.map_data(dps)
                self._update_map_tracking()
                _LOGGER.debug("Received MQTT data: %s", dps)
        except Exception as err:
            _LOGGER.error("Error processing MQTT message: %s", err)

    def _on_disconnect(self, client, userdata, rc):
        """Handle MQTT disconnection."""
        self._connected = False
        _LOGGER.warning("Disconnected from MQTT broker with code %d", rc)

    async def update(self) -> None:
        """Update device data via HTTP API."""
        work_status = self.get_work_status()
        # Ask until a floor exists. A single request at startup is easy to miss,
        # and saved room names used to stop the retries.
        if work_status in _ACTIVE_MAP_STATUSES or not self.has_map_stream():
            await self.request_map_download()

        headers = {
            "user-agent": "EufyHome-Android-3.1.3-753",
            "timezone": "Europe/Berlin",
            "openudid": self._openudid,
            "language": "en",
            "country": "US",
            "os-version": "Android",
            "model-type": "PHONE",
            "app-name": "eufy_home",
            "x-auth-token": self._user_info.get("user_center_token", ""),
            "gtoken": self._user_info.get("gtoken", ""),
            "content-type": "application/json; charset=UTF-8",
        }

        try:
            async with self._session.post(
                "https://aiot-clean-api-pr.eufylife.com/app/devicerelation/get_device_list",
                headers=headers,
                json={"attribute": 3},
            ) as resp:
                result = await resp.json()
                data = result.get("data", result)

                if data.get("devices"):
                    for device_obj in data["devices"]:
                        device = device_obj.get("device", device_obj)
                        device_sn = device.get("device_sn", device.get("id", ""))

                        if device_sn == self._device_id:
                            dps = device.get("dps", {})
                            self.map_data(dps)
                            break
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to update MQTT device %s: %s", self._device_id, err)

    async def send_command(self, data: dict[str, Any]) -> None:
        """Send command via MQTT."""
        if not self._mqtt_client or not self._connected:
            _LOGGER.error("MQTT not connected, cannot send command")
            return

        try:
            import time

            user_id = self._mqtt_credentials.get("user_id", "")
            app_name = self._mqtt_credentials.get("app_name", "eufy_home")
            client_id = f"android-{app_name}-eufy_android_{self._openudid}_{user_id}"

            payload = json.dumps(
                {
                    "account_id": user_id,
                    "data": data,
                    "device_sn": self._device_id,
                    "protocol": 2,
                    "t": int(time.time() * 1000),
                }
            )

            mqtt_message = {
                "head": {
                    "client_id": client_id,
                    "cmd": 65537,
                    "cmd_status": 1,
                    "msg_seq": 2,
                    "seed": "",
                    "sess_id": client_id,
                    "sign_code": 0,
                    "timestamp": int(time.time() * 1000),
                    "version": "1.0.0.1",
                },
                "payload": payload,
            }

            topic_req = f"cmd/eufy_home/{self._device_model}/{self._device_id}/req"
            topic_smart = f"smart/mb/out/{self._device_id}"

            self._mqtt_client.publish(topic_req, json.dumps(mqtt_message))
            self._mqtt_client.publish(topic_smart, json.dumps(mqtt_message))

            _LOGGER.debug("Sent MQTT command to %s: %s", self._device_id, data)
        except Exception as err:
            _LOGGER.error("Failed to send MQTT command: %s", err)

    async def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
            self._connected = False

        import os

        for path in (self._cert_path, self._key_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self._cert_path = None
        self._key_path = None
