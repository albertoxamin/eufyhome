#!/usr/bin/env python3
"""
Decode all map-related DPS data from the device.
Uses data already fetched via HTTP API (no MQTT, no commands).
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUTPUT_DIR = REPO_ROOT / "scripts" / "captured_data"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_credentials() -> tuple[str, str]:
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
        print("Missing credentials.")
        sys.exit(1)
    return username, password


def decode_protobuf_full(data: bytes, depth: int = 0, max_depth: int = 5) -> list[dict]:
    """Recursively decode protobuf fields with full detail."""
    from eufy_clean.api.proto_utils import decode_protobuf_field, decode_varint

    fields = []
    pos = 0

    while pos < len(data):
        field_num, wire_type, value, new_pos = decode_protobuf_field(data, pos)
        if field_num is None:
            break
        pos = new_pos

        field = {"field": field_num, "wire_type": wire_type}

        if wire_type == 0:  # varint
            field["type"] = "varint"
            field["value"] = value
            # Also show as signed (zigzag decoded)
            field["as_signed"] = (value >> 1) ^ -(value & 1)
        elif wire_type == 1:  # fixed64
            field["type"] = "fixed64"
            field["value"] = value
            try:
                field["as_double"] = struct.unpack("<d", struct.pack("<Q", value))[0]
            except Exception:
                pass
        elif wire_type == 5:  # fixed32
            field["type"] = "fixed32"
            field["value"] = value
            try:
                field["as_float"] = struct.unpack("<f", struct.pack("<I", value))[0]
            except Exception:
                pass
        elif wire_type == 2:  # length-delimited
            field["type"] = "bytes"
            field["length"] = len(value)
            field["hex"] = value.hex()

            # Try as UTF-8 string
            try:
                text = value.decode("utf-8")
                if text.isprintable() or len(text) < 50:
                    field["as_string"] = text
            except Exception:
                pass

            # Try nested protobuf
            if depth < max_depth and len(value) >= 2:
                try:
                    nested = decode_protobuf_full(value, depth + 1, max_depth)
                    if nested:
                        # Verify it consumed the data reasonably
                        field["nested"] = nested
                except Exception:
                    pass

        fields.append(field)

    return fields


def decode_with_length_prefix(data: bytes) -> list[dict]:
    """Try decoding with and without length prefix."""
    from eufy_clean.api.proto_utils import decode_varint

    # Try with length prefix first
    if len(data) >= 2:
        ln, pos_after = decode_varint(data, 0)
        if 0 < ln <= len(data) - pos_after and ln == len(data) - pos_after:
            return decode_protobuf_full(data[pos_after:])

    return decode_protobuf_full(data)


def print_protobuf_tree(fields: list[dict], indent: int = 0) -> None:
    """Pretty-print decoded protobuf fields as a tree."""
    prefix = "  " * indent
    for f in fields:
        fnum = f["field"]
        ftype = f["type"]

        if ftype == "varint":
            val = f["value"]
            signed = f.get("as_signed", "")
            extra = f" (signed: {signed})" if signed != val else ""
            print(f"{prefix}field {fnum}: varint = {val}{extra}")
        elif ftype in ("fixed32", "fixed64"):
            val = f["value"]
            extra = ""
            if "as_float" in f:
                extra = f" (float: {f['as_float']:.6f})"
            elif "as_double" in f:
                extra = f" (double: {f['as_double']:.6f})"
            print(f"{prefix}field {fnum}: {ftype} = {val}{extra}")
        elif ftype == "bytes":
            length = f["length"]
            hex_str = f.get("hex", "")
            as_str = f.get("as_string")

            if "nested" in f:
                print(f"{prefix}field {fnum}: message ({length} bytes) {{")
                print_protobuf_tree(f["nested"], indent + 1)
                print(f"{prefix}}}")
            elif as_str is not None and len(as_str) > 0 and as_str.isprintable():
                print(f"{prefix}field {fnum}: string = {as_str!r}")
            else:
                print(f"{prefix}field {fnum}: bytes ({length}) = {hex_str[:64]}{'...' if len(hex_str) > 64 else ''}")


async def main() -> None:
    import types

    username, password = load_credentials()

    sys.modules["eufy_clean"] = types.ModuleType("eufy_clean")
    sys.modules["eufy_clean.api"] = types.ModuleType("eufy_clean.api")
    _load_module("eufy_clean.const", COMPONENT_ROOT / "const.py")
    _load_module("eufy_clean.api.proto_utils", COMPONENT_ROOT / "api" / "proto_utils.py")
    _load_module("eufy_clean.api.eufy_api", COMPONENT_ROOT / "api" / "eufy_api.py")
    from eufy_clean.api.eufy_api import EufyCleanApi

    print("=" * 60)
    print("Decode Map-Related DPS Data")
    print("=" * 60)

    api = EufyCleanApi(username=username, password=password)
    await api.login()

    devices = await api.get_all_devices()
    if not devices:
        print("No devices found.")
        await api.close()
        return

    device = devices[0]
    device_id = device["device_id"]
    device_model = device["device_model"]
    dps = device.get("dps", {})
    print(f"Device: {device.get('device_name', device_id)} ({device_model})")

    # Map-related DPS keys to decode
    map_keys = {
        "164": "robovac_map_v2",
        "165": "robovac_map",
        "166": "robovac_path",
        "169": "robovac_map_v3",
        "170": "robovac_ai_config / MAP_DATA",
        "171": "robovac_map_manage",
        "172": "robovac_clean_prefer",
        "179": "robovac_zone_clean",
    }

    for key, name in map_keys.items():
        value = dps.get(key)
        print(f"\n{'─' * 60}")
        print(f"DPS {key}: {name}")
        print(f"{'─' * 60}")

        if value is None:
            print("  (None / not present)")
            continue

        if not isinstance(value, str) or len(value) < 4:
            print(f"  Raw value: {value!r}")
            continue

        try:
            raw = base64.b64decode(value)
            print(f"  Base64: {value}")
            print(f"  Decoded: {len(raw)} bytes")
            print(f"  Hex: {raw.hex()}")
            print()

            fields = decode_with_length_prefix(raw)
            if fields:
                print("  Protobuf decode:")
                print_protobuf_tree(fields, indent=2)
            else:
                print("  (no protobuf fields decoded)")
        except Exception as e:
            print(f"  Decode error: {e}")

    await api.close()

    # Save all decoded data
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_decoded = {}
    for key, name in map_keys.items():
        value = dps.get(key)
        if value and isinstance(value, str) and len(value) >= 4:
            try:
                raw = base64.b64decode(value)
                fields = decode_with_length_prefix(raw)
                all_decoded[key] = {
                    "name": name,
                    "base64": value,
                    "size": len(raw),
                    "hex": raw.hex(),
                    "fields": fields,
                }
            except Exception:
                pass

    (OUTPUT_DIR / "map_dps_decoded.json").write_text(
        json.dumps(all_decoded, indent=2, default=str)
    )
    print(f"\nAll decoded data saved to {OUTPUT_DIR / 'map_dps_decoded.json'}")


if __name__ == "__main__":
    asyncio.run(main())
