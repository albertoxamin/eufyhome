"""Shared fixtures for eufyhome tests.

The plain unit tests (map rendering, protocol encoding) run without Home
Assistant installed. In that case the ``custom_components`` namespace
packages are registered by hand, so ``custom_components.eufy_clean.api``
imports without executing the integration's ``__init__`` (which needs
Home Assistant).

When Home Assistant *is* installed (the HA harness, see
requirements_test.txt), the shim is skipped and the real integration
package is imported from the repo root (``pythonpath = ["."]`` in
pyproject.toml), with the ``hass`` fixture from pytest-homeassistant.
"""

import sys
import types
from pathlib import Path

try:
    import homeassistant  # noqa: F401
except ImportError:
    _HA_INSTALLED = False
else:
    _HA_INSTALLED = True

if not _HA_INSTALLED:
    _COMPONENTS = Path(__file__).resolve().parents[1] / "custom_components"

    def _package(name: str, path: Path) -> None:
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module

    _package("custom_components", _COMPONENTS)
    _package("custom_components.eufy_clean", _COMPONENTS / "eufy_clean")
    _package("custom_components.eufy_clean.api", _COMPONENTS / "eufy_clean" / "api")
else:
    import pytest
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from homeassistant.core import HomeAssistant
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant.common import MockConfigEntry

    from custom_components.eufy_clean.const import DOMAIN

    @pytest.fixture
    def hass_config_dir(hass_tmp_config_dir):
        """Give each HA test an isolated, writable config directory."""
        return hass_tmp_config_dir

    @pytest.fixture(autouse=True)
    async def _setup_http(hass: HomeAssistant):
        """Serve the integration's map card and preview over HTTP.

        The full frontend component is not set up: it needs the
        hass_frontend build, which is not a pip package in 2026.11.
        The integration only needs the frontend's URL manager to exist
        so ``add_extra_js_url`` can register the map card, so seed it
        directly (this is what frontend's own setup does).
        """
        from homeassistant.components.frontend import (
            DATA_EXTRA_MODULE_URL,
            UrlManager,
        )

        assert await async_setup_component(hass, "http", {})
        hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_args: None, [])

    class _StubCoordinator:
        """Stand-in for EufyCleanDataUpdateCoordinator: no network, no devices."""

        def __init__(self, hass, entry, api):
            self.hass = hass
            self.entry = entry
            self.api = api
            self.devices: dict = {}
            self.data: dict = {}
            self.shutdown_called = False

        async def async_setup(self) -> bool:
            return True

        async def async_config_entry_first_refresh(self) -> None:
            return None

        async def async_shutdown(self) -> None:
            self.shutdown_called = True

        def apply_map_options(self) -> None:
            return None

    @pytest.fixture
    def mock_eufy_api():
        """Patch the API client and coordinator with no-network stubs.

        Yields the stub coordinator class so a test can tweak its behaviour
        (e.g. make ``async_setup`` return False to exercise the retry path).
        """
        from unittest.mock import MagicMock, patch

        with (
            patch(
                "custom_components.eufy_clean.EufyCleanDataUpdateCoordinator",
                _StubCoordinator,
            ),
            patch("custom_components.eufy_clean.EufyCleanApi", MagicMock()),
        ):
            yield _StubCoordinator

    @pytest.fixture
    def mock_config_entry() -> MockConfigEntry:
        """Return a config entry for the integration."""
        return MockConfigEntry(
            domain=DOMAIN,
            title="Eufy Clean (user@example.com)",
            data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"},
        )
