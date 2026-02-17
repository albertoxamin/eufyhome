"""Data update coordinator for Eufy Clean."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EufyCleanApi
from .api.controllers import BaseDevice, CloudDevice, MqttDevice
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class EufyCleanDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Eufy Clean data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: EufyCleanApi,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.api = api
        self.entry = entry
        self.devices: dict[str, BaseDevice] = {}
        self._session: aiohttp.ClientSession | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Eufy Clean API."""
        try:
            # Update all devices
            for device_id, device in self.devices.items():
                try:
                    await device.update()
                except Exception as err:
                    _LOGGER.error("Error updating device %s: %s", device_id, err)

            # Return aggregated device data
            return {
                device_id: {
                    "battery_level": device.get_battery_level(),
                    "state": device.get_state(),
                    "work_status": device.get_work_status(),
                    "work_mode": device.get_work_mode(),
                    "clean_speed": device.get_clean_speed(),
                    "error_code": device.get_error_code(),
                    "is_charging": device.is_charging(),
                    "is_docked": device.is_docked(),
                    "volume": device.get_volume(),
                    "scenes": device.get_scenes(),
                    "dnd": device.get_dnd(),
                    "boost_iq": device.get_boost_iq(),
                    "cleaning_statistics": device.get_cleaning_statistics(),
                    "consumables": device.get_consumables(),
                    "station_status": device.get_station_status(),
                }
                for device_id, device in self.devices.items()
            }
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def async_setup(self) -> bool:
        """Set up the coordinator."""
        try:
            # Login to API
            await self.api.login()

            # Get all devices
            devices = await self.api.get_all_devices()

            if not devices:
                _LOGGER.warning("No devices found")
                return False

            # Create session for devices
            self._session = aiohttp.ClientSession()

            # Initialize device controllers
            for device_data in devices:
                device_id = device_data.get("device_id", "")
                if not device_id:
                    continue

                is_mqtt = device_data.get("mqtt", False)

                if is_mqtt:
                    device = MqttDevice(
                        device_config=device_data,
                        mqtt_credentials=self.api.mqtt_credentials,
                        openudid=self.api.openudid,
                        user_info=self.api.user_info,
                        session=self._session,
                    )
                else:
                    device = CloudDevice(
                        device_config=device_data,
                        session=self._session,
                        access_token=self.api._access_token,
                        openudid=self.api.openudid,
                    )

                # Connect to device
                await device.connect()
                self.devices[device_id] = device

                _LOGGER.info(
                    "Initialized device: %s (%s) - %s",
                    device.device_name,
                    device.device_id,
                    "MQTT" if is_mqtt else "Cloud",
                )

            return True

        except Exception as err:
            _LOGGER.error("Error setting up coordinator: %s", err)
            return False

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        # Disconnect all MQTT devices
        for device in self.devices.values():
            if isinstance(device, MqttDevice):
                await device.disconnect()

        # Close session
        if self._session and not self._session.closed:
            await self._session.close()

        # Close API session
        await self.api.close()

    def get_device(self, device_id: str) -> BaseDevice | None:
        """Get a device by ID."""
        return self.devices.get(device_id)
