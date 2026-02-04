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
    EUFY_CLEAN_WORK_STATUS,
    LEGACY_DPS_MAP,
    NOVEL_DPS_MAP,
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
        for key, value in dps.items():
            for map_key, map_value in self._dps_map.items():
                if map_value == key:
                    self._robovac_data[map_key] = value
        
        _LOGGER.debug("Mapped data: %s", self._robovac_data)
        self._notify_update()

    def get_battery_level(self) -> int:
        """Get battery level."""
        return int(self._robovac_data.get("BATTERY_LEVEL", 0))

    def get_clean_speed(self) -> str:
        """Get current clean speed."""
        speed = self._robovac_data.get("CLEAN_SPEED", "standard")
        
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
        
        if self._novel_api and status:
            # For novel API, decode the protobuf status
            # Simplified version - just return raw status
            try:
                if isinstance(status, str):
                    return status.lower()
            except Exception:
                pass
        
        if isinstance(status, str):
            return status.lower()
        
        return "charging"

    def get_work_mode(self) -> str:
        """Get current work mode."""
        mode = self._robovac_data.get("WORK_MODE", "")
        
        if isinstance(mode, str):
            return mode.lower()
        
        return "auto"

    def get_state(self) -> str:
        """Get vacuum state for Home Assistant."""
        work_status = self.get_work_status()
        work_mode = self.get_work_mode()
        
        state = EUFY_CLEAN_GET_STATE.get(work_status)
        if not state:
            state = EUFY_CLEAN_GET_STATE.get(work_mode, "idle")
        
        return state

    def get_error_code(self) -> str | int:
        """Get current error code."""
        error = self._robovac_data.get("ERROR_CODE", 0)
        
        if isinstance(error, int):
            return EUFY_CLEAN_ERROR_CODES.get(error, f"unknown_error_{error}")
        
        return error if error else "none"

    def is_charging(self) -> bool:
        """Check if device is charging."""
        return self.get_work_status() == "charging"

    def is_docked(self) -> bool:
        """Check if device is docked."""
        state = self.get_state()
        return state in ("docked", "idle")

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
            # Encode control command for novel API
            command = self._encode_control_command(EUFY_CLEAN_CONTROL["START_AUTO_CLEAN"])
            await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        else:
            await self.send_command({self._dps_map["WORK_MODE"]: "auto"})
            await self.send_command({self._dps_map["PLAY_PAUSE"]: True})

    async def pause(self) -> None:
        """Pause cleaning."""
        if self._novel_api:
            command = self._encode_control_command(EUFY_CLEAN_CONTROL["PAUSE_TASK"])
            await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        else:
            await self.send_command({self._dps_map["PLAY_PAUSE"]: False})

    async def stop(self) -> None:
        """Stop cleaning."""
        if self._novel_api:
            command = self._encode_control_command(EUFY_CLEAN_CONTROL["STOP_TASK"])
            await self.send_command({self._dps_map["PLAY_PAUSE"]: command})
        else:
            await self.send_command({self._dps_map["PLAY_PAUSE"]: False})

    async def return_to_base(self) -> None:
        """Return to charging base."""
        if self._novel_api:
            command = self._encode_control_command(EUFY_CLEAN_CONTROL["START_GOHOME"])
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

    def _encode_control_command(self, method: int) -> str:
        """Encode control command for novel API."""
        # Simplified encoding - just base64 encode the method
        # In production, this should use protobuf
        data = {"method": method}
        return base64.b64encode(json.dumps(data).encode()).decode()


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
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as cert_file:
                    cert_file.write(cert_pem)
                    cert_path = cert_file.name
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as key_file:
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
            
            payload = json.dumps({
                "account_id": user_id,
                "data": data,
                "device_sn": self._device_id,
                "protocol": 2,
                "t": int(time.time() * 1000),
            })
            
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
