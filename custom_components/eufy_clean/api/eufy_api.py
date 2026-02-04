"""Eufy Clean API implementation."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from typing import Any

import aiohttp

from ..const import EUFY_CLEAN_DEVICES, NOVEL_DPS_MAP

_LOGGER = logging.getLogger(__name__)


class EufyCleanApi:
    """Eufy Clean API client."""

    def __init__(self, username: str, password: str) -> None:
        """Initialize the API client."""
        self._username = username
        self._password = password
        self._openudid = secrets.token_hex(16)
        self._session: aiohttp.ClientSession | None = None
        self._access_token: str | None = None
        self._user_info: dict[str, Any] | None = None
        self._mqtt_credentials: dict[str, Any] | None = None
        self._cloud_devices: list[dict[str, Any]] = []
        self._mqtt_devices: list[dict[str, Any]] = []
        self._eufy_devices: list[dict[str, Any]] = []

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the API session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def login(self) -> dict[str, Any]:
        """Login to Eufy API and get credentials."""
        session = await self._get_session()
        
        # Login to Eufy
        headers = {
            "category": "Home",
            "Accept": "*/*",
            "openudid": self._openudid,
            "Accept-Language": "en-US;q=1",
            "Content-Type": "application/json",
            "clientType": "1",
            "language": "en",
            "User-Agent": "EufyHome-iOS-2.14.0-6",
            "timezone": "Europe/Berlin",
            "country": "US",
            "Connection": "keep-alive",
        }
        
        data = {
            "email": self._username,
            "password": self._password,
            "client_id": "eufyhome-app",
            "client_secret": "GQCpr9dSp3uQpsOMgJ4xQ",
        }
        
        try:
            async with session.post(
                "https://home-api.eufylife.com/v1/user/email/login",
                headers=headers,
                json=data,
            ) as resp:
                result = await resp.json()
                if result.get("access_token"):
                    self._access_token = result["access_token"]
                    _LOGGER.info("Eufy login successful")
                else:
                    _LOGGER.error("Login failed: %s", result)
                    raise Exception(f"Login failed: {result}")
        except aiohttp.ClientError as err:
            _LOGGER.error("Login failed: %s", err)
            raise

        # Get user info
        await self._get_user_info()
        
        # Get MQTT credentials
        await self._get_mqtt_credentials()
        
        return {
            "access_token": self._access_token,
            "user_info": self._user_info,
            "mqtt_credentials": self._mqtt_credentials,
        }

    async def _get_user_info(self) -> None:
        """Get user center info."""
        session = await self._get_session()
        
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
            async with session.get(
                "https://api.eufylife.com/v1/user/user_center_info",
                headers=headers,
            ) as resp:
                result = await resp.json()
                self._user_info = result
                if result.get("user_center_id"):
                    self._user_info["gtoken"] = hashlib.md5(
                        result["user_center_id"].encode()
                    ).hexdigest()
                _LOGGER.debug("Got user info")
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to get user info: %s", err)
            raise

    async def _get_mqtt_credentials(self) -> None:
        """Get MQTT credentials."""
        session = await self._get_session()
        
        headers = {
            "content-type": "application/json",
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
        }
        
        try:
            async with session.post(
                "https://aiot-clean-api-pr.eufylife.com/app/devicemanage/get_user_mqtt_info",
                headers=headers,
            ) as resp:
                result = await resp.json()
                self._mqtt_credentials = result.get("data", {})
                _LOGGER.debug("Got MQTT credentials")
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to get MQTT credentials: %s", err)
            raise

    async def get_cloud_devices(self) -> list[dict[str, Any]]:
        """Get devices from Eufy Cloud API."""
        session = await self._get_session()
        
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
            async with session.get(
                "https://api.eufylife.com/v1/device/v2",
                headers=headers,
            ) as resp:
                result = await resp.json()
                data = result.get("data", result)
                devices = data.get("devices", [])
                self._eufy_devices = devices
                _LOGGER.info("Found %d devices via Eufy Cloud", len(devices))
                return devices
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to get cloud devices: %s", err)
            return []

    async def get_mqtt_devices(self) -> list[dict[str, Any]]:
        """Get devices that use MQTT (newer models like X10)."""
        session = await self._get_session()
        
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
            async with session.post(
                "https://aiot-clean-api-pr.eufylife.com/app/devicerelation/get_device_list",
                headers=headers,
                json={"attribute": 3},
            ) as resp:
                result = await resp.json()
                data = result.get("data", result)
                
                devices = []
                if data.get("devices"):
                    for device_obj in data["devices"]:
                        devices.append(device_obj.get("device", device_obj))
                
                _LOGGER.info("Found %d devices via MQTT API", len(devices))
                return devices
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to get MQTT devices: %s", err)
            return []

    async def get_all_devices(self) -> list[dict[str, Any]]:
        """Get all devices (cloud + MQTT)."""
        await self.get_cloud_devices()
        
        # Get MQTT devices
        mqtt_devices = await self.get_mqtt_devices()
        
        all_devices = []
        
        # Process MQTT devices
        for device in mqtt_devices:
            device_id = device.get("device_sn", device.get("id", ""))
            if not device_id:
                continue
                
            dps = device.get("dps", {})
            api_type = self._check_api_type(dps)
            
            device_info = self._find_device_model(device_id)
            if device_info.get("invalid"):
                continue
                
            all_devices.append({
                "device_id": device_id,
                "device_model": device_info.get("device_model", ""),
                "device_name": device_info.get("device_name", f"Eufy {device_id}"),
                "device_model_name": device_info.get("device_model_name", ""),
                "api_type": api_type,
                "mqtt": True,
                "dps": dps,
            })
        
        self._mqtt_devices = all_devices
        return all_devices

    def _check_api_type(self, dps: dict[str, Any]) -> str:
        """Check if device uses novel or legacy API."""
        if any(k in dps for k in NOVEL_DPS_MAP.values()):
            return "novel"
        return "legacy"

    def _find_device_model(self, device_id: str) -> dict[str, Any]:
        """Find device model from eufy devices list."""
        for device in self._eufy_devices:
            if device.get("id") == device_id:
                product = device.get("product", {})
                product_code = product.get("product_code", "")[:5]
                device_model = device.get("device_model", "")[:5]
                model_code = product_code or device_model
                
                return {
                    "device_id": device_id,
                    "device_model": model_code,
                    "device_name": device.get("alias_name") or device.get("device_name") or device.get("name", ""),
                    "device_model_name": EUFY_CLEAN_DEVICES.get(model_code, product.get("name", "")),
                    "invalid": False,
                }
        
        return {"device_id": device_id, "device_model": "", "device_name": "", "device_model_name": "", "invalid": True}

    @property
    def mqtt_credentials(self) -> dict[str, Any] | None:
        """Get MQTT credentials."""
        return self._mqtt_credentials

    @property
    def user_info(self) -> dict[str, Any] | None:
        """Get user info."""
        return self._user_info

    @property
    def openudid(self) -> str:
        """Get openudid."""
        return self._openudid
