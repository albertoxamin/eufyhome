"""Support for Eufy Clean map camera."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api.controllers import BaseDevice
from .const import DOMAIN, EUFY_CLEAN_DEVICES, MANUFACTURER
from .coordinator import EufyCleanDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Map pixel colors (RGBA)
PIXEL_COLORS = {
    0: (128, 128, 128, 255),  # UNKNOWN - Gray
    1: (0, 0, 0, 255),  # OBSTACLE - Black
    2: (255, 255, 255, 255),  # FREE - White
    3: (173, 216, 230, 255),  # CARPET - Light Blue
}

# Room colors for room outline
ROOM_COLORS = [
    (255, 179, 186, 255),  # Light Pink
    (255, 223, 186, 255),  # Light Orange
    (255, 255, 186, 255),  # Light Yellow
    (186, 255, 201, 255),  # Light Green
    (186, 225, 255, 255),  # Light Blue
    (219, 186, 255, 255),  # Light Purple
    (255, 186, 255, 255),  # Light Magenta
    (186, 255, 255, 255),  # Light Cyan
]


def decompress_lz4(data: bytes, original_size: int) -> bytes:
    """Decompress LZ4 data."""
    try:
        import lz4.block

        return lz4.block.decompress(data, uncompressed_size=original_size)
    except ImportError:
        _LOGGER.debug("lz4 library not available, map decompression disabled")
        # Return empty bytes - map will show placeholder
        return b""
    except Exception as err:
        _LOGGER.debug("Error decompressing LZ4 data: %s", err)
        return b""


def parse_map_pixels(data: bytes, width: int, height: int) -> list[list[int]]:
    """
    Parse map pixel data.
    Each byte contains 4 pixels (2 bits per pixel).
    """
    pixels = []

    for byte in data:
        # Extract 4 pixels from each byte (2 bits each, from low to high)
        pixels.append(byte & 0x03)
        pixels.append((byte >> 2) & 0x03)
        pixels.append((byte >> 4) & 0x03)
        pixels.append((byte >> 6) & 0x03)

    # Reshape into 2D array
    map_2d = []
    for y in range(height):
        row = []
        for x in range(width):
            idx = y * width + x
            if idx < len(pixels):
                row.append(pixels[idx])
            else:
                row.append(0)
        map_2d.append(row)

    return map_2d


def create_map_image(
    map_data: list[list[int]],
    width: int,
    height: int,
    robot_pos: tuple[int, int] | None = None,
    dock_pos: tuple[int, int] | None = None,
) -> bytes:
    """Create PNG image from map data."""
    try:
        from PIL import Image, ImageDraw

        # Create image
        img = Image.new("RGBA", (width, height), (200, 200, 200, 255))

        # Draw pixels
        for y, row in enumerate(map_data):
            for x, pixel in enumerate(row):
                color = PIXEL_COLORS.get(pixel, PIXEL_COLORS[0])
                img.putpixel((x, y), color)

        # Draw dock position
        if dock_pos:
            draw = ImageDraw.Draw(img)
            dx, dy = dock_pos
            # Draw a green square for dock
            draw.rectangle([dx - 3, dy - 3, dx + 3, dy + 3], fill=(0, 200, 0, 255))

        # Draw robot position
        if robot_pos:
            draw = ImageDraw.Draw(img)
            rx, ry = robot_pos
            # Draw a red circle for robot
            draw.ellipse([rx - 4, ry - 4, rx + 4, ry + 4], fill=(255, 0, 0, 255))

        # Scale up for better visibility
        scale = 4
        img = img.resize((width * scale, height * scale), Image.NEAREST)

        # Save to bytes
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    except ImportError:
        _LOGGER.warning("PIL library not available, cannot create map image")
        return create_placeholder_image()
    except Exception as err:
        _LOGGER.error("Error creating map image: %s", err)
        return create_placeholder_image()


def create_placeholder_image() -> bytes:
    """Create a placeholder image when map is not available."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (400, 300), (240, 240, 240))
        draw = ImageDraw.Draw(img)

        # Draw text
        text = "Map not available"
        draw.text((200, 150), text, fill=(128, 128, 128), anchor="mm")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    except ImportError:
        # Return a minimal valid PNG if PIL is not available
        return b""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eufy Clean camera from a config entry."""
    coordinator: EufyCleanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id, device in coordinator.devices.items():
        entities.append(EufyCleanMapCamera(coordinator, device))

    async_add_entities(entities)


class EufyCleanMapCamera(Camera):
    """Camera entity for Eufy Clean map."""

    _attr_has_entity_name = True
    _attr_name = "Map"

    def __init__(
        self,
        coordinator: EufyCleanDataUpdateCoordinator,
        device: BaseDevice,
    ) -> None:
        """Initialize the camera."""
        super().__init__()
        self._coordinator = coordinator
        self._device = device
        self._attr_unique_id = f"{device.device_id}_map"
        self._attr_is_streaming = False
        self._attr_is_recording = False
        self._map_image: bytes | None = None
        self._last_map_data: dict[str, Any] | None = None

        model_name = EUFY_CLEAN_DEVICES.get(device.device_model, device.device_model)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name or f"Eufy {model_name}",
            manufacturer=MANUFACTURER,
            model=model_name,
            sw_version=device.device_model,
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._coordinator.async_add_listener(self._handle_coordinator_update)

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        await super().async_will_remove_from_hass()
        self._coordinator.async_remove_listener(self._handle_coordinator_update)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return camera image."""
        # Try to get map data from device
        map_data = await self._get_map_data()

        if map_data:
            return await self.hass.async_add_executor_job(self._create_image, map_data)

        # Return placeholder if no map
        return await self.hass.async_add_executor_job(create_placeholder_image)

    async def _get_map_data(self) -> dict[str, Any] | None:
        """Get map data from device."""
        if not hasattr(self._device, "_robovac_data"):
            return None
        robovac_data = self._device._robovac_data
        # Map-related DPS keys per API spec: 170=map_edit, 171=multi_maps_ctrl, 172=multi_maps_mng
        map_keys = ["MAP_DATA", "170", "171", "172"]
        for key in map_keys:
            if key in robovac_data:
                parsed = self._parse_map_response(robovac_data[key])
                if parsed and parsed.get("pixels"):
                    return parsed
        return None

    def _parse_map_response(self, data: Any) -> dict[str, Any] | None:
        """Parse map response from device."""
        if not data:
            return None
        if isinstance(data, str):
            try:
                decoded = base64.b64decode(data)
                return self._parse_map_protobuf(decoded)
            except Exception as err:
                _LOGGER.debug("Error parsing map data: %s", err)
        return None

    def _parse_map_protobuf(self, data: bytes) -> dict[str, Any] | None:
        """Parse map protobuf adaptively — collect all varints and blobs, infer dimensions."""
        import math

        try:
            from .api.proto_utils import decode_varint, decode_protobuf_field
        except ImportError:
            _LOGGER.debug("proto_utils not available for map parsing")
            return None

        varints: list[int] = []
        blobs: list[bytes] = []

        def _collect(msg: bytes) -> None:
            """Recursively collect varints and large blobs from protobuf message."""
            pos = 0
            # Strip optional length prefix
            if len(msg) >= 2:
                ln, pos_after = decode_varint(msg, 0)
                if 0 < ln <= len(msg) - pos_after:
                    msg = msg[pos_after : pos_after + ln]
            while pos < len(msg):
                field_num, wire_type, value, pos = decode_protobuf_field(msg, pos)
                if field_num is None:
                    break
                if wire_type == 0:
                    varints.append(value)
                elif wire_type == 2 and isinstance(value, bytes):
                    if len(value) > 50:
                        # Recurse into nested messages to find inner map blob
                        try:
                            _collect(value)
                        except Exception:
                            blobs.append(value)
                    else:
                        blobs.append(value)

        try:
            _collect(data)

            if not blobs:
                return None

            # Largest blob is the most likely candidate for pixel data
            pixel_bytes = max(blobs, key=len)
            num_pixels = len(pixel_bytes) * 4  # 2 bits per pixel → 4 pixels per byte

            # Try LZ4 decompression for several plausible target sizes
            for expected in (num_pixels, 512 * 512, 256 * 256, 1024 * 1024):
                expected_bytes = (expected + 3) // 4
                if 0 < expected_bytes <= 1024 * 1024 and expected_bytes >= len(pixel_bytes) // 2:
                    decompressed = decompress_lz4(pixel_bytes, expected_bytes)
                    if decompressed and len(decompressed) == expected_bytes:
                        pixel_bytes = decompressed
                        num_pixels = len(pixel_bytes) * 4
                        break

            # Infer dimensions from reasonable varints or pixel count
            candidates = sorted((v for v in varints if 8 <= v <= 2048), reverse=True)
            if len(candidates) >= 2:
                width, height = candidates[0], candidates[1]
                if width * height > num_pixels * 2 or width * height < num_pixels // 2:
                    width = height = int(math.isqrt(num_pixels)) or 256
            elif len(candidates) == 1:
                width = height = candidates[0]
            else:
                width = height = int(math.isqrt(num_pixels)) or 256

            if width <= 0 or height <= 0:
                width = height = 256

            pixels = parse_map_pixels(pixel_bytes, width, height)
            return {
                "width": width,
                "height": height,
                "pixels": pixels,
                "robot_pos": None,
                "dock_pos": None,
            }
        except Exception as err:
            _LOGGER.debug("Error parsing map protobuf: %s", err)
            return None

    def _create_image(self, map_data: dict[str, Any]) -> bytes:
        """Create map image from data."""
        width = map_data.get("width", 100)
        height = map_data.get("height", 100)
        pixels = map_data.get("pixels", [])
        robot_pos = map_data.get("robot_pos")
        dock_pos = map_data.get("dock_pos")

        if pixels:
            return create_map_image(pixels, width, height, robot_pos, dock_pos)

        return create_placeholder_image()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "map_available": self._map_image is not None,
        }
