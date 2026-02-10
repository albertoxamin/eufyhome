"""Device controllers for Eufy Clean."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
from typing import Any, Callable

import aiohttp

from ..const import (
    EUFY_CLEAN_CONTROL,
    EUFY_CLEAN_ERROR_CODES,
    EUFY_CLEAN_GET_STATE,
    EUFY_CLEAN_SPEEDS,
    EUFY_CLEAN_SUPPORTS_CLEAN_TYPE,
    EUFY_CLEAN_WORK_STATUS,
    LEGACY_DPS_MAP,
    NOVEL_DPS_MAP,
)
from .proto_utils import (
    decode_clean_speed,
    decode_cleaning_statistics,
    decode_consumables,
    decode_dnd,
    decode_error_code,
    decode_scene_list,
    decode_work_status,
    encode_control_command,
    encode_clean_param,
    encode_dnd,
    encode_room_clean_command,
    encode_scene_clean_command,
    is_base64_encoded,
    CONTROL_START_AUTO_CLEAN,
    CONTROL_START_GOHOME,
    CONTROL_START_SCENE_CLEAN,
    CONTROL_STOP_TASK,
    CONTROL_PAUSE_TASK,
    CONTROL_RESUME_TASK,
    CLEAN_TYPE_SWEEP_ONLY,
    CLEAN_TYPE_MOP_ONLY,
    CLEAN_TYPE_SWEEP_AND_MOP,
    MOP_LEVEL_LOW,
    MOP_LEVEL_MEDIUM,
    MOP_LEVEL_HIGH,
    CLEAN_EXTENT_NORMAL,
    CLEAN_EXTENT_NARROW,
    CLEAN_EXTENT_QUICK,
)

_LOGGER = logging.getLogger(__name__)


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
        """Add callback for data updates."""
        self._update_callbacks.append(callback)

    def _notify_update(self) -> None:
        """Notify all callbacks of data update."""
        for callback in self._update_callbacks:
            try:
                callback()
            except Exception as err:
                _LOGGER.error("Error in update callback: %s", err)

    def map_data(self, dps: dict[str, Any]) -> None:
        """Map DPS data to robovac data."""
        mapped_values = set(self._dps_map.values())
        for key, value in dps.items():
            for map_key, map_value in self._dps_map.items():
                if map_value == key:
                    self._robovac_data[map_key] = value
                    break
            else:
                # Store unmapped DPS keys by raw key so map/camera can use them
                if key not in mapped_values:
                    self._robovac_data[key] = value
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
        status = self._robovac_data.get("WORK_STATUS", "")

        if self._novel_api and isinstance(status, str) and is_base64_encoded(status):
            decoded = decode_work_status(status)
            return decoded.get("state", "charging")

        if isinstance(status, str):
            return status.lower()

        return "charging"

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
                error_code = decoded["errors"][0]
                return EUFY_CLEAN_ERROR_CODES.get(error_code, error_text)
            elif decoded.get("warnings"):
                warn_code = decoded["warnings"][0]
                return EUFY_CLEAN_ERROR_CODES.get(warn_code, error_text)

            return error_text

        if isinstance(error, int):
            return EUFY_CLEAN_ERROR_CODES.get(error, f"unknown_error_{error}")

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

    async def start(self) -> None:
        """Start cleaning."""
        if self._novel_api:
            # Encode control command for novel API using protobuf
            command = encode_control_command(
                CONTROL_START_AUTO_CLEAN, {"clean_times": 1}
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

    async def clean_rooms(self, room_ids: list[int], clean_times: int = 1) -> None:
        """Start cleaning specific rooms by their IDs."""
        if not self._novel_api:
            _LOGGER.warning("Room cleaning not supported on legacy devices")
            return

        if not room_ids:
            _LOGGER.error("No room IDs provided")
            return

        command = encode_room_clean_command(room_ids, clean_times)
        await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        _LOGGER.info("Started cleaning rooms: %s", room_ids)

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
            return {"enabled": False, "start_hour": 22, "end_hour": 8}
        return decode_dnd(raw)

    async def set_dnd(self, enabled: bool, start_hour: int, end_hour: int) -> None:
        """Set Do Not Disturb status and schedule."""
        command = encode_dnd(enabled, start_hour, end_hour)
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
            return {"total_cleans": 0, "total_area": 0, "total_time_min": 0, "total_sessions": 0}
        return decode_cleaning_statistics(raw)

    def get_consumables(self) -> dict[str, Any]:
        """Get consumable/accessory life percentages."""
        raw = self._robovac_data.get("ACCESSORIES_STATUS", "")
        if not raw or not isinstance(raw, str):
            return {"rolling_brush": 0, "side_brush": 0, "filter": 0, "mop_pad": 0,
                    "other_brush": 0, "sensor": 0, "runtime_hours": 0}
        return decode_consumables(raw)

    def get_rooms(self) -> list[dict[str, Any]]:
        """Get list of available rooms from device data."""
        # Rooms are typically stored in ROOM_PARAMS or similar
        # This would need to be populated from the device's map data
        rooms = self._robovac_data.get("ROOMS", [])
        return rooms


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
    ) -> None:
        """Initialize the MQTT device."""
        super().__init__(device_config)
        self._mqtt_credentials = mqtt_credentials
        self._openudid = openudid
        self._user_info = user_info
        self._session = session
        self._mqtt_client = None
        self._connected = False

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
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".pem", delete=False
                ) as cert_file:
                    cert_file.write(cert_pem)
                    cert_path = cert_file.name

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".key", delete=False
                ) as key_file:
                    key_file.write(private_key)
                    key_path = key_file.name

                self._mqtt_client.tls_set(
                    certfile=cert_path,
                    keyfile=key_path,
                )

            # Set callbacks
            self._mqtt_client.on_connect = self._on_connect
            self._mqtt_client.on_message = self._on_message
            self._mqtt_client.on_disconnect = self._on_disconnect

            # Connect
            endpoint = self._mqtt_credentials.get("endpoint_addr", "")
            if endpoint:
                self._mqtt_client.connect_async(endpoint, 8883)
                self._mqtt_client.loop_start()

        except Exception as err:
            _LOGGER.error("Failed to connect MQTT: %s", err)

    def _on_connect(self, client, userdata, flags, rc):
        """Handle MQTT connection."""
        if rc == 0:
            _LOGGER.info("Connected to MQTT broker")
            self._connected = True

            # Subscribe to device topics
            topic_res = f"cmd/eufy_home/{self._device_model}/{self._device_id}/res"
            topic_smart = f"smart/mb/in/{self._device_id}"

            client.subscribe(topic_res)
            client.subscribe(topic_smart)
            _LOGGER.debug("Subscribed to %s and %s", topic_res, topic_smart)
        else:
            _LOGGER.error("MQTT connection failed with code %d", rc)

    def _on_message(self, client, userdata, msg):
        """Handle MQTT message."""
        try:
            payload = json.loads(msg.payload.decode())
            data = payload.get("payload", {})

            if isinstance(data, str):
                data = json.loads(data)

            dps = data.get("data", {})
            if dps:
                self.map_data(dps)
                _LOGGER.debug("Received MQTT data: %s", dps)
        except Exception as err:
            _LOGGER.error("Error processing MQTT message: %s", err)

    def _on_disconnect(self, client, userdata, rc):
        """Handle MQTT disconnection."""
        self._connected = False
        _LOGGER.warning("Disconnected from MQTT broker with code %d", rc)

    async def update(self) -> None:
        """Update device data via HTTP API."""
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
