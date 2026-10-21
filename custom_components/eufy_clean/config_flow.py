"""Config flow for Eufy Clean integration."""

from __future__ import annotations

import io
import logging
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant import config_entries
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import EufyCleanApi
from .const import (
    CONF_MAP_ROTATION,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_MAP_ROTATION,
    DOMAIN,
    MAP_ROTATION_OPTIONS,
)

_PREVIEW_VIEW = "map_preview_view"

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.EMAIL,
            ),
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
            ),
        ),
    }
)


class EufyCleanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eufy Clean."""

    VERSION = 1

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EufyCleanOptionsFlowHandler:
        """Return the options flow handler."""
        return EufyCleanOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Check if already configured
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            # Try to login
            api = EufyCleanApi(username, password)
            try:
                await api.login()
                devices = await api.get_all_devices()
                await api.close()

                if not devices:
                    errors["base"] = "no_devices"
                else:
                    return self.async_create_entry(
                        title=f"Eufy Clean ({username})",
                        data={
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                        },
                    )
            except Exception as err:
                _LOGGER.error("Error connecting to Eufy: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauthorization request."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauthorization confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            api = EufyCleanApi(username, password)
            try:
                await api.login()
                await api.close()

                # Update the existing entry
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                if entry:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                        },
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")
            except Exception as err:
                _LOGGER.error("Error during reauth: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


def _rgb(hex_color: str) -> list[int]:
    hex_color = hex_color.removeprefix("#")
    return [int(hex_color[i : i + 2], 16) for i in (0, 2, 4)]


def _color(_key: str, _fallback: str) -> selector.ColorRGBSelector:
    return selector.ColorRGBSelector()


def _number(min_value: float, max_value: float, step: float) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_value,
            max=max_value,
            step=step,
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _choice(options: list[dict[str, str]]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


_DEFAULTS: dict[str, Any] = {
    "c0": _rgb("1e1e1e"),
    "c1": _rgb("141414"),
    "c2": _rgb("c8c8c8"),
    "c3": _rgb("c8c8c8"),
    "r1": _rgb("6496c8"),
    "r2": _rgb("96c882"),
    "r3": _rgb("c8a082"),
    "r4": _rgb("b48cc8"),
    "r5": _rgb("c8be6e"),
    "r6": _rgb("8cbec8"),
    "r7": _rgb("c88296"),
    "r8": _rgb("a0c8b4"),
    "path": _rgb("ffffff"),
    "path_style": "segmented",
    "path_width": 2,
    "radius": 1.9,
    "map_rotation": DEFAULT_MAP_ROTATION,
    "robot": "dot",
    "status": "none",
    "max_px": 512,
    "dpi": 2,
    "label": _rgb("1e1e1e"),
    "label_bg": _rgb("ffffff"),
    "label_size": 8,
    "path_on": True,
    "trail": True,
    "labels": True,
    "zones": True,
    "dock": True,
    "robot_on": True,
    "next_action": "update_preview",
}


def _form_values(options: dict[str, Any]) -> dict[str, Any]:
    values = dict(_DEFAULTS)
    for key, value in options.items():
        if key in values and key != "next_action":
            values[key] = value
    if not values.get("status"):
        values["status"] = "none"
    return values


def _placeholder_png() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (480, 180), (30, 30, 30))
    ImageDraw.Draw(image).text((24, 80), "No map yet", fill=(230, 230, 230))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class MapPreviewView(HomeAssistantView):
    """Serve the options-flow map image. The flow id is the access token."""

    url = "/api/eufy_clean/map_preview/{flow_id}"
    name = "api:eufy_clean:map_preview"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, flow_id: str) -> web.Response:
        flow = self.hass.config_entries.options._progress.get(flow_id)  # noqa: SLF001
        png = getattr(flow, "preview_png", None) if flow else None
        if not png:
            raise web.HTTPNotFound
        return web.Response(body=png, content_type="image/png")


def async_register_map_preview(hass: HomeAssistant) -> None:
    """Register the preview image route once."""
    if hass.data.get(DOMAIN, {}).get(_PREVIEW_VIEW):
        return
    hass.http.register_view(MapPreviewView(hass))
    hass.data.setdefault(DOMAIN, {})[_PREVIEW_VIEW] = True


class EufyCleanOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle map display options."""

    def __init__(self) -> None:
        self.preview_png: bytes = b""
        self._preview_version = 0

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage map rendering options."""
        if user_input is not None and user_input.get("next_action") == "save":
            data = {key: value for key, value in user_input.items() if key != "next_action"}
            if data.get("status") == "none":
                data["status"] = ""
            return self.async_create_entry(title="", data=data)

        draft = user_input or _form_values(dict(self.config_entry.options))
        await self._render_preview(draft)
        return self._show(draft)

    def _show(self, draft: dict[str, Any]) -> FlowResult:
        flow_id = self.flow_id or ""
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(_schema(), draft),
            description_placeholders={
                "preview_url": (
                    f"/api/eufy_clean/map_preview/{flow_id}?v={self._preview_version}"
                )
            },
        )

    async def _render_preview(self, draft: dict[str, Any]) -> None:
        device = None
        for value in self.hass.data.get(DOMAIN, {}).values():
            devices = getattr(value, "devices", None)
            if not devices:
                continue
            for candidate in devices.values():
                if getattr(candidate, "render_preview", None):
                    device = candidate
                    break
        if device is None:
            self.preview_png = await self.hass.async_add_executor_job(_placeholder_png)
        else:
            image = await self.hass.async_add_executor_job(device.render_preview, draft)
            self.preview_png = image or await self.hass.async_add_executor_job(
                _placeholder_png
            )
        self._preview_version += 1


def _schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional("c0"): _color("c0", "1e1e1e"),
            vol.Optional("c1"): _color("c1", "141414"),
            vol.Optional("c2"): _color("c2", "c8c8c8"),
            vol.Optional("c3"): _color("c3", "c8c8c8"),
            vol.Optional("r1"): _color("r1", "6496c8"),
            vol.Optional("r2"): _color("r2", "96c882"),
            vol.Optional("r3"): _color("r3", "c8a082"),
            vol.Optional("r4"): _color("r4", "b48cc8"),
            vol.Optional("r5"): _color("r5", "c8be6e"),
            vol.Optional("r6"): _color("r6", "8cbec8"),
            vol.Optional("r7"): _color("r7", "c88296"),
            vol.Optional("r8"): _color("r8", "a0c8b4"),
            vol.Optional("path"): _color("path", "ffffff"),
            vol.Optional("path_style"): _choice(
                [
                    {"value": "segmented", "label": "Segmented"},
                    {"value": "dotted", "label": "Dotted"},
                    {"value": "solid", "label": "Solid"},
                ]
            ),
            vol.Optional("path_width"): _number(1, 8, 1),
            vol.Optional("radius"): _number(0.2, 4, 0.1),
            vol.Optional(CONF_MAP_ROTATION): vol.In(MAP_ROTATION_OPTIONS),
            vol.Optional("robot"): _choice(
                [
                    {"value": "dot", "label": "Dot"},
                    {"value": "googly", "label": "Googly"},
                ]
            ),
            vol.Optional("status"): _choice(
                [
                    {"value": "none", "label": "None"},
                    {"value": "charging", "label": "Charging"},
                    {"value": "washing", "label": "Washing"},
                    {"value": "drying", "label": "Drying"},
                    {"value": "emptying", "label": "Emptying"},
                ]
            ),
            vol.Optional("max_px"): _number(128, 1024, 64),
            vol.Optional("dpi"): _number(1, 4, 1),
            vol.Optional("label"): _color("label", "1e1e1e"),
            vol.Optional("label_bg"): _color("label_bg", "ffffff"),
            vol.Optional("label_size"): _number(8, 22, 1),
            vol.Optional("path_on"): selector.BooleanSelector(),
            vol.Optional("trail"): selector.BooleanSelector(),
            vol.Optional("labels"): selector.BooleanSelector(),
            vol.Optional("zones"): selector.BooleanSelector(),
            vol.Optional("dock"): selector.BooleanSelector(),
            vol.Optional("robot_on"): selector.BooleanSelector(),
            vol.Required("next_action"): _choice(
                [
                    {"value": "update_preview", "label": "Update preview"},
                    {"value": "save", "label": "Save"},
                ]
            ),
        }
    )
