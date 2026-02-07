#!/usr/bin/env python3
"""
Test script for Eufy Clean integration.

Uses credentials from environment variables or from test_credentials.env:
  EUFY_USERNAME  - Eufy account email
  EUFY_PASSWORD  - Eufy account password

Run from repo root:
  python scripts/test_eufy_clean.py

Map: the script only sees what get_device_list returns in each device's "dps".
That API may not include the actual floor map (map might be a separate endpoint or
MQTT-only). If DPS contains keys 165/169/etc., we try to decode them as a map and
save scripts/map_preview_<device_id>.png (requires Pillow; optional lz4). The result
may be wrong if the payload is not a floor map (e.g. room list or path data).

Or with env vars:
  EUFY_USERNAME=you@example.com EUFY_PASSWORD=secret python scripts/test_eufy_clean.py
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import os
import sys
from pathlib import Path

# Repo and integration paths
REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# DPS keys that may contain map data (Eufy/Tuya)
MAP_DPS_KEYS = ("165", "169", "164", "166")

# Map pixel colors (RGBA) - same as camera.py
PIXEL_COLORS = {
    0: (128, 128, 128, 255),  # UNKNOWN - Gray
    1: (0, 0, 0, 255),  # OBSTACLE - Black
    2: (255, 255, 255, 255),  # FREE - White
    3: (173, 216, 230, 255),  # CARPET - Light Blue
}


def _load_module(name: str, path: Path):
    """Load a module from file without running package __init__.py."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_credentials() -> tuple[str, str]:
    """Load username and password from env or test_credentials.env."""
    creds_file = REPO_ROOT / "test_credentials.env"
    if creds_file.exists():
        for line in creds_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key == "EUFY_USERNAME":
                    os.environ.setdefault("EUFY_USERNAME", value)
                elif key == "EUFY_PASSWORD":
                    os.environ.setdefault("EUFY_PASSWORD", value)

    username = os.environ.get("EUFY_USERNAME", "").strip()
    password = os.environ.get("EUFY_PASSWORD", "").strip()
    if not username or not password:
        print(
            "Missing credentials. Set EUFY_USERNAME and EUFY_PASSWORD, or create\n"
            "test_credentials.env in the repo root with:\n"
            "  EUFY_USERNAME=your@email.com\n"
            "  EUFY_PASSWORD=yourpassword"
        )
        sys.exit(1)
    return username, password


def _decompress_lz4(data: bytes, original_size: int) -> bytes:
    """Decompress LZ4 data."""
    try:
        import lz4.block

        return lz4.block.decompress(data, uncompressed_size=original_size)
    except Exception:
        return b""


def _parse_map_pixels(data: bytes, width: int, height: int) -> list[list[int]]:
    """Parse map pixel data (2 bits per pixel, 4 pixels per byte)."""
    pixels = []
    for byte in data:
        pixels.append(byte & 0x03)
        pixels.append((byte >> 2) & 0x03)
        pixels.append((byte >> 4) & 0x03)
        pixels.append((byte >> 6) & 0x03)
    map_2d = []
    for y in range(height):
        row = []
        for x in range(width):
            idx = y * width + x
            row.append(pixels[idx] if idx < len(pixels) else 0)
        map_2d.append(row)
    return map_2d


def _parse_map_protobuf(data: bytes):
    """Parse RVC-style map protobuf; returns (width, height, pixel_bytes) or None."""
    import math
    from eufy_clean.api.proto_utils import decode_varint, decode_protobuf_field

    varints: list[int] = []
    blobs: list[bytes] = []

    def collect(msg: bytes) -> None:
        pos = 0
        if len(msg) >= 2:
            ln, pos_after = decode_varint(msg, 0)
            if pos_after + ln <= len(msg):
                msg = msg[pos_after : pos_after + ln]
        while pos < len(msg):
            field_num, wire_type, value, pos = decode_protobuf_field(msg, pos)
            if field_num is None:
                break
            if wire_type == 0:
                varints.append(value)
            elif wire_type == 2 and isinstance(value, bytes):
                if len(value) > 50:
                    # Recurse into nested message to find inner map blob
                    try:
                        collect(value)
                    except Exception:
                        blobs.append(value)
                else:
                    blobs.append(value)

    try:
        collect(data)
        if not blobs:
            return None
        # Largest blob is likely the map pixel data
        pixel_bytes = max(blobs, key=len)
        # Try LZ4 if size suggests compressed
        num_pixels = len(pixel_bytes) * 4
        for expected in (num_pixels, (512 * 512), (256 * 256), (1024 * 1024)):
            expected_bytes = (expected + 3) // 4
            if (
                0 < expected_bytes <= 1024 * 1024
                and expected_bytes >= len(pixel_bytes) // 2
            ):
                try:
                    decompressed = _decompress_lz4(pixel_bytes, expected_bytes)
                    if decompressed and len(decompressed) == expected_bytes:
                        pixel_bytes = decompressed
                        num_pixels = len(pixel_bytes) * 4
                        break
                except Exception:
                    pass
        # Dimensions: use two largest reasonable varints, or infer from pixel count (square map)
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
        return (width, height, pixel_bytes)
    except Exception:
        return None


def _create_map_png(
    pixels: list[list[int]], width: int, height: int, scale: int = 4
) -> bytes | None:
    """Create PNG bytes from map pixels."""
    try:
        from PIL import Image

        img = Image.new("RGBA", (width, height), (200, 200, 200, 255))
        for y, row in enumerate(pixels):
            for x, pixel in enumerate(row):
                color = PIXEL_COLORS.get(pixel, PIXEL_COLORS[0])
                img.putpixel((x, y), color)
        img = img.resize((width * scale, height * scale), getattr(Image, "NEAREST", 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _try_save_map_preview(
    device_id: str, device_name: str, raw_value: str
) -> Path | None:
    """Decode map DPS value, render PNG, save to scripts/map_preview_<id>.png. Returns path or None."""
    if not raw_value or not isinstance(raw_value, str):
        return None
    try:
        data = base64.b64decode(raw_value)
    except Exception:
        return None
    parsed = _parse_map_protobuf(data)
    if not parsed:
        return None
    width, height, pixel_bytes = parsed
    pixels = _parse_map_pixels(pixel_bytes, width, height)
    png_bytes = _create_map_png(pixels, width, height)
    if not png_bytes:
        return None
    out_dir = REPO_ROOT / "scripts"
    safe_id = "".join(c if c.isalnum() else "_" for c in device_id)[:32]
    path = out_dir / f"map_preview_{safe_id}.png"
    path.write_bytes(png_bytes)
    return path


async def main() -> None:
    """Run the test."""
    username, password = load_credentials()

    # Load only API and const (avoid homeassistant/__init__.py)
    import types

    sys.modules["eufy_clean"] = types.ModuleType("eufy_clean")
    sys.modules["eufy_clean.api"] = types.ModuleType("eufy_clean.api")
    _load_module("eufy_clean.const", COMPONENT_ROOT / "const.py")
    _load_module(
        "eufy_clean.api.proto_utils", COMPONENT_ROOT / "api" / "proto_utils.py"
    )
    _load_module("eufy_clean.api.eufy_api", COMPONENT_ROOT / "api" / "eufy_api.py")
    from eufy_clean.api.eufy_api import EufyCleanApi
    from eufy_clean.const import EUFY_CLEAN_DEVICES

    print("Logging in to Eufy...")
    api = EufyCleanApi(username=username, password=password)
    try:
        await api.login()
        devices = await api.get_all_devices()
    finally:
        await api.close()

    if not devices:
        print("No devices found.")
        return

    print(f"\nFound {len(devices)} device(s):\n")
    saved_previews: list[Path] = []
    for d in devices:
        device_id = d.get("device_id", "")
        name = d.get("device_name", "—")
        model = d.get("device_model", "")
        api_type = d.get("api_type", "legacy")
        model_name = EUFY_CLEAN_DEVICES.get(model, model or "—")
        # From API (DPS decode) when available
        supports_clean_type = d.get("supports_clean_type", False)

        is_novel = api_type == "novel"
        show_clean_type = is_novel and supports_clean_type
        show_mop_level = is_novel and supports_clean_type
        show_clean_extent = is_novel

        print(f"  Device: {name}")
        print(f"    ID:    {device_id}")
        print(f"    Model: {model} ({model_name})")
        print(f"    API:   {api_type}")
        print(f"    Clean Type select:   {'yes' if show_clean_type else 'no'}")
        print(f"    Mop Level select:    {'yes' if show_mop_level else 'no'}")
        print(f"    Clean Extent select: {'yes' if show_clean_extent else 'no'}")

        # Map: get_device_list may not return the real floor map (might be another API/MQTT)
        dps = d.get("dps", {})
        map_keys_found = [k for k in MAP_DPS_KEYS if k in dps and dps[k]]
        if map_keys_found:
            parts = []
            for k in map_keys_found:
                val = dps[k]
                size = len(val) if isinstance(val, str) else 0
                parts.append(f"'{k}' ({size} chars)")
            print(
                f"    DPS keys 165/169:   present — {', '.join(parts)} (may not be floor map)"
            )
            for k in map_keys_found:
                raw = dps[k]
                if isinstance(raw, str):
                    preview_path = _try_save_map_preview(device_id, name, raw)
                    if preview_path:
                        saved_previews.append(preview_path)
                        print(
                            f"    Map preview:         saved (experimental) to {preview_path}"
                        )
                        break
            else:
                print(
                    "    Map preview:         not generated (decode failed or format unknown)"
                )
        else:
            print("    DPS keys 165/169:   not present")

        print()

    # Open first map preview with default image viewer if we saved one
    if saved_previews:
        first = saved_previews[0]
        if sys.platform == "darwin":
            os.system(f'open "{first}"')
        elif sys.platform == "linux":
            os.system(f'xdg-open "{first}" 2>/dev/null')
        print(f"Opened map preview: {first}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
