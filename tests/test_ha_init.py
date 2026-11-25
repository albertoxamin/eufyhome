"""HA harness tests: config entry setup, unload, and setup retry.

These need a real Home Assistant (see requirements_test.txt); they are
skipped automatically when the harness is not installed.
"""

import typing
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.eufy_clean import async_setup_entry, async_unload_entry
from custom_components.eufy_clean.const import DOMAIN


def test_entry_functions_return_none() -> None:
    """The 2026.11 contract: setup/unload return None, or raise."""
    assert typing.get_type_hints(async_setup_entry).get("return") is type(None)
    assert typing.get_type_hints(async_unload_entry).get("return") is type(None)


async def test_setup_entry_loads(
    hass: HomeAssistant, mock_config_entry, mock_eufy_api
) -> None:
    """Setting up the entry loads it and stores the coordinator."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    assert isinstance(coordinator, mock_eufy_api)
    assert coordinator.devices == {}


async def test_unload_entry(
    hass: HomeAssistant, mock_config_entry, mock_eufy_api
) -> None:
    """Unloading the entry shuts the coordinator down and unloads it."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert coordinator.shutdown_called


async def test_setup_failure_goes_to_retry(
    hass: HomeAssistant, mock_config_entry, mock_eufy_api, monkeypatch
) -> None:
    """A coordinator that cannot connect leaves the entry in SETUP_RETRY."""
    monkeypatch.setattr(mock_eufy_api, "async_setup", AsyncMock(return_value=False))
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
