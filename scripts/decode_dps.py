#!/usr/bin/env python3
"""
Decode specific DPS values from the device in detail.

Fetches current DPS via REST and recursively decodes protobuf structure.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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


def decode_protobuf_recursive(data: bytes, indent: int = 0) -> None:
    """Recursively decode and print protobuf fields."""
    from eufy_clean.api.proto_utils import decode_varint, decode_protobuf_field

    prefix = "  " * indent
    pos = 0

    while pos < len(data):
        field_num, wire_type, value, new_pos = decode_protobuf_field(data, pos)
        if field_num is None:
            break
        pos = new_pos

        wire_names = {0: "varint", 1: "fixed64", 2: "bytes", 5: "fixed32"}
        wt_name = wire_names.get(wire_type, f"wt{wire_type}")

        if wire_type == 0:
            print(f"{prefix}field {field_num} ({wt_name}): {value}")
        elif wire_type == 2 and isinstance(value, bytes):
            # Try to decode as UTF-8 string
            try:
                text = value.decode("utf-8")
                if all(32 <= ord(c) < 127 for c in text):
                    print(f"{prefix}field {field_num} (string, {len(value)}B): \"{text}\"")
                    continue
            except (UnicodeDecodeError, ValueError):
                pass

            # Try to decode as nested protobuf
            if len(value) >= 2:
                try:
                    # Quick validation: try parsing first field
                    test_fn, test_wt, test_val, test_pos = decode_protobuf_field(value, 0)
                    if test_fn is not None and 1 <= test_fn <= 100 and test_wt in (0, 1, 2, 5):
                        print(f"{prefix}field {field_num} (message, {len(value)}B):")
                        decode_protobuf_recursive(value, indent + 1)
                        continue
                except Exception:
                    pass

            # Raw bytes
            hex_preview = value[:32].hex()
            if len(value) > 32:
                hex_preview += "..."
            print(f"{prefix}field {field_num} (bytes, {len(value)}B): {hex_preview}")
        elif wire_type == 1:
            print(f"{prefix}field {field_num} (fixed64): {value}")
        elif wire_type == 5:
            print(f"{prefix}field {field_num} (fixed32): {value}")


def decode_dps_value(key: str, raw_b64: str) -> None:
    """Decode a single DPS base64 value."""
    from eufy_clean.api.proto_utils import decode_varint

    print(f"\n{'='*60}")
    print(f"DPS {key}: {len(raw_b64)} chars base64")

    try:
        data = base64.b64decode(raw_b64)
    except Exception as e:
        print(f"  base64 decode failed: {e}")
        return

    print(f"  Decoded: {len(data)} bytes")
    print(f"  Hex: {data[:64].hex()}{'...' if len(data) > 64 else ''}")

    # Strip length prefix if present
    if len(data) >= 2:
        ln, pos_after = decode_varint(data, 0)
        if 0 < ln == len(data) - pos_after:
            print(f"  Length prefix: {ln} (matches remaining data)")
            data = data[pos_after:]
        else:
            print(f"  No length prefix (first varint={ln}, remaining={len(data) - pos_after})")

    print(f"  Protobuf fields:")
    decode_protobuf_recursive(data, indent=2)


async def main() -> None:
    username, password = load_credentials()

    import types
    sys.modules["eufy_clean"] = types.ModuleType("eufy_clean")
    sys.modules["eufy_clean.api"] = types.ModuleType("eufy_clean.api")
    _load_module("eufy_clean.const", COMPONENT_ROOT / "const.py")
    _load_module("eufy_clean.api.proto_utils", COMPONENT_ROOT / "api" / "proto_utils.py")
    _load_module("eufy_clean.api.eufy_api", COMPONENT_ROOT / "api" / "eufy_api.py")
    from eufy_clean.api.eufy_api import EufyCleanApi

    print("Logging in to Eufy...")
    api = EufyCleanApi(username=username, password=password)
    try:
        await api.login()
        devices = await api.get_all_devices()
    finally:
        await api.close()

    if not devices:
        print("No devices.")
        return

    device = devices[0]
    dps = device.get("dps", {})
    print(f"Device: {device.get('device_name')} ({device.get('device_model')})")

    # Decode requested keys — focus on scenes (180) but do all interesting ones
    targets = sys.argv[1:] if len(sys.argv) > 1 else ["180"]
    for key in targets:
        raw = dps.get(key)
        if raw is None:
            print(f"\nDPS {key}: not present")
        elif isinstance(raw, str) and len(raw) >= 4:
            decode_dps_value(key, raw)
        else:
            print(f"\nDPS {key}: {type(raw).__name__} = {raw}")


if __name__ == "__main__":
    asyncio.run(main())
