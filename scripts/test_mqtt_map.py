#!/usr/bin/env python3
"""
Test script: connect to Eufy MQTT and listen for map data.

Uses the same credentials as test_eufy_clean.py.
Connects to the MQTT broker, subscribes to device topics,
and dumps every DPS update — looking specifically for large
payloads on map-related keys (170, 171, 172).

Run from repo root:
  python scripts/test_mqtt_map.py
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import os
import ssl
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# How long to listen for MQTT messages (seconds)
LISTEN_SECONDS = 60

# DPS keys we care about for map data (per API spec)
MAP_DPS_KEYS = {"170", "171", "172"}

# All DPS updates received, keyed by DPS id
received_dps: dict[str, list] = {}


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
        print("Missing credentials. See test_eufy_clean.py for setup.")
        sys.exit(1)
    return username, password


def try_decode_protobuf_summary(raw_b64: str) -> str:
    """Try to decode base64 protobuf and return a short summary."""
    try:
        from eufy_clean.api.proto_utils import decode_varint, decode_protobuf_field

        data = base64.b64decode(raw_b64)
        # Strip length prefix
        if len(data) >= 2:
            ln, pos_after = decode_varint(data, 0)
            if 0 < ln <= len(data) - pos_after:
                data = data[pos_after : pos_after + ln]

        fields = []
        pos = 0
        while pos < len(data):
            field_num, wire_type, value, pos = decode_protobuf_field(data, pos)
            if field_num is None:
                break
            if wire_type == 0:
                fields.append(f"f{field_num}:varint={value}")
            elif wire_type == 2 and isinstance(value, bytes):
                fields.append(f"f{field_num}:bytes({len(value)})")
            elif wire_type == 1:
                fields.append(f"f{field_num}:fixed64={value}")
            elif wire_type == 5:
                fields.append(f"f{field_num}:fixed32={value}")
        return " ".join(fields) if fields else f"raw({len(data)}B)"
    except Exception as e:
        return f"decode_err: {e}"


def try_save_map(dps_key: str, raw_b64: str, device_id: str) -> Path | None:
    """Attempt to parse and render map data, save as PNG."""
    try:
        from eufy_clean.api.proto_utils import decode_varint, decode_protobuf_field
        import math

        data = base64.b64decode(raw_b64)
        if len(data) < 50:
            return None

        varints: list[int] = []
        blobs: list[bytes] = []

        def collect(msg: bytes) -> None:
            pos = 0
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
                        try:
                            collect(value)
                        except Exception:
                            blobs.append(value)
                    else:
                        blobs.append(value)

        collect(data)
        if not blobs:
            return None

        pixel_bytes = max(blobs, key=len)
        num_pixels = len(pixel_bytes) * 4

        # Try LZ4 decompression
        for expected in (num_pixels, 512 * 512, 256 * 256, 1024 * 1024):
            expected_bytes = (expected + 3) // 4
            if 0 < expected_bytes <= 1024 * 1024 and expected_bytes >= len(pixel_bytes) // 2:
                try:
                    import lz4.block
                    decompressed = lz4.block.decompress(pixel_bytes, uncompressed_size=expected_bytes)
                    if decompressed and len(decompressed) == expected_bytes:
                        pixel_bytes = decompressed
                        num_pixels = len(pixel_bytes) * 4
                        break
                except Exception:
                    pass

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
            return None

        # Render
        from PIL import Image
        PIXEL_COLORS = {
            0: (128, 128, 128, 255),
            1: (0, 0, 0, 255),
            2: (255, 255, 255, 255),
            3: (173, 216, 230, 255),
        }

        img = Image.new("RGBA", (width, height), (200, 200, 200, 255))
        pixels = []
        for byte in pixel_bytes:
            pixels.append(byte & 0x03)
            pixels.append((byte >> 2) & 0x03)
            pixels.append((byte >> 4) & 0x03)
            pixels.append((byte >> 6) & 0x03)

        for y in range(height):
            for x in range(width):
                idx = y * width + x
                if idx < len(pixels):
                    color = PIXEL_COLORS.get(pixels[idx], PIXEL_COLORS[0])
                    img.putpixel((x, y), color)

        scale = 4
        img = img.resize((width * scale, height * scale), getattr(Image, "NEAREST", 0))
        safe_id = "".join(c if c.isalnum() else "_" for c in device_id)[:32]
        out = REPO_ROOT / "scripts" / f"mqtt_map_dps{dps_key}_{safe_id}.png"
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.write_bytes(buf.getvalue())
        return out
    except Exception as e:
        print(f"    Map render failed: {e}")
        return None


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
    await api.login()

    devices = await api.get_all_devices()
    if not devices:
        print("No devices found.")
        await api.close()
        return

    mqtt_creds = api.mqtt_credentials
    if not mqtt_creds:
        print("No MQTT credentials available.")
        await api.close()
        return

    device = devices[0]
    device_id = device["device_id"]
    device_model = device["device_model"]
    print(f"Device: {device.get('device_name', device_id)} ({device_model})")
    print(f"MQTT endpoint: {mqtt_creds.get('endpoint_addr', '?')}")

    # Set up MQTT
    import paho.mqtt.client as mqtt

    openudid = api.openudid
    user_id = mqtt_creds.get("user_id", "")
    app_name = mqtt_creds.get("app_name", "eufy_home")
    client_id = f"android-{app_name}-eufy_android_{openudid}_{user_id}"

    topic_res = f"cmd/eufy_home/{device_model}/{device_id}/res"
    topic_smart = f"smart/mb/in/{device_id}"

    connected_event = asyncio.Event()
    message_count = 0

    def on_connect(client, userdata, flags, reason_code, properties):
        nonlocal connected_event
        if reason_code == 0 or str(reason_code) == "Success":
            print(f"Connected to MQTT broker")
            client.subscribe(topic_res)
            client.subscribe(topic_smart)
            # Wildcard to catch any other topics
            client.subscribe(f"cmd/eufy_home/{device_model}/{device_id}/#")
            client.subscribe(f"smart/#")
            print(f"Subscribed to:\n  {topic_res}\n  {topic_smart}\n  + wildcards")
            print(f"\nListening for {LISTEN_SECONDS}s... (waiting for DPS updates)\n")
        else:
            print(f"MQTT connection failed: rc={reason_code}")

    def on_message(client, userdata, msg):
        nonlocal message_count
        message_count += 1
        topic = msg.topic
        payload_size = len(msg.payload)
        print(f"--- Message #{message_count} on {topic} ({payload_size} bytes) ---")

        try:
            payload = json.loads(msg.payload.decode())

            data = payload.get("payload", {})
            if isinstance(data, str):
                data = json.loads(data)

            dps = data.get("data", {})
            if not dps:
                print(f"  No DPS data in payload. Keys: {list(payload.keys())}")
                # Print payload structure
                for k, v in payload.items():
                    if k == "head":
                        print(f"  head.cmd = {v.get('cmd', '?')}")
                    elif k == "payload":
                        if isinstance(v, str) and len(v) > 200:
                            print(f"  payload: str({len(v)} chars)")
                        else:
                            print(f"  payload: {v!r:.200s}")
                return

            print(f"  DPS keys: {sorted(dps.keys(), key=lambda x: int(x) if x.isdigit() else x)}")

            for k in sorted(dps.keys(), key=lambda x: int(x) if x.isdigit() else x):
                v = dps[k]
                if v is None:
                    continue

                # Track received data
                received_dps.setdefault(k, []).append(v)

                is_map_key = k in MAP_DPS_KEYS
                marker = " *** MAP KEY ***" if is_map_key else ""

                if isinstance(v, str):
                    size = len(v)
                    decoded_size = len(base64.b64decode(v)) if size >= 4 else 0
                    summary = try_decode_protobuf_summary(v) if size >= 4 else v
                    print(f"  DPS {k:>4s}: str({size} chars, ~{decoded_size}B decoded){marker}")
                    print(f"           {summary:.120s}")

                    # If this is a map key with substantial data, try rendering
                    if is_map_key and decoded_size > 50:
                        print(f"           Attempting map render...")
                        path = try_save_map(k, v, device_id)
                        if path:
                            print(f"           SAVED: {path}")
                        else:
                            print(f"           (too small or parse failed)")
                elif isinstance(v, bool):
                    print(f"  DPS {k:>4s}: bool = {v}{marker}")
                elif isinstance(v, (int, float)):
                    print(f"  DPS {k:>4s}: num  = {v}{marker}")
                else:
                    print(f"  DPS {k:>4s}: {type(v).__name__} = {v!r:.80s}{marker}")

        except Exception as e:
            print(f"  Parse error: {e}")
            # Dump raw
            raw = msg.payload[:500]
            print(f"  Raw: {raw}")

        print()

    def on_disconnect(client, userdata, flags, reason_code, properties):
        print(f"Disconnected from MQTT: rc={reason_code}")

    # Create MQTT client (v2 API)
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )

    # TLS with client certificate
    cert_pem = mqtt_creds.get("certificate_pem", "")
    private_key = mqtt_creds.get("private_key", "")

    if cert_pem and private_key:
        cert_file = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
        cert_file.write(cert_pem)
        cert_file.close()

        key_file = tempfile.NamedTemporaryFile(mode="w", suffix=".key", delete=False)
        key_file.write(private_key)
        key_file.close()

        client.tls_set(
            certfile=cert_file.name,
            keyfile=key_file.name,
        )
        # Don't verify server hostname against cert (Eufy uses AWS IoT custom endpoints)
        client.tls_insecure_set(True)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    endpoint = mqtt_creds.get("endpoint_addr", "")
    if not endpoint:
        print("No MQTT endpoint in credentials.")
        await api.close()
        return

    print(f"Connecting to {endpoint}:8883 ...")
    try:
        client.connect(endpoint, 8883, keepalive=60)
    except Exception as e:
        print(f"MQTT connect failed: {e}")
        await api.close()
        return

    client.loop_start()

    # Wait for connection
    await asyncio.sleep(3)

    # Build MQTT command helper
    def send_dps_command(dps_data: dict) -> None:
        """Send a DPS command via MQTT."""
        payload_inner = json.dumps({
            "account_id": user_id,
            "data": dps_data,
            "device_sn": device_id,
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
            "payload": payload_inner,
        }
        topic_req = f"cmd/eufy_home/{device_model}/{device_id}/req"
        topic_out = f"smart/mb/out/{device_id}"
        client.publish(topic_req, json.dumps(mqtt_message))
        client.publish(topic_out, json.dumps(mqtt_message))

    # Try requesting map data via various approaches:
    from eufy_clean.api.proto_utils import encode_varint, encode_protobuf_field

    # 1. Request MultiMapsCtrl (DPS 171) — empty message to ask for current map list
    print("Sending map requests...")
    empty_msg = encode_varint(0)  # length-prefixed empty
    empty_b64 = base64.b64encode(b"\x00").decode()

    # Try DPS 171 (multi_maps_ctrl) with an empty/get request
    send_dps_command({"171": empty_b64})
    print("  Sent DPS 171 (multi_maps_ctrl) request")

    # 2. Request MapEdit (DPS 170) — try empty request
    send_dps_command({"170": empty_b64})
    print("  Sent DPS 170 (map_edit) request")

    # 3. Request MultiMapsManage (DPS 172) — try empty request
    send_dps_command({"172": empty_b64})
    print("  Sent DPS 172 (multi_maps_mng) request")

    # 4. Also try requesting device info (DPS 169) to trigger any state update
    send_dps_command({"169": empty_b64})
    print("  Sent DPS 169 (app_dev_info) request")

    # 5. Try protobuf-encoded requests with method=0 (often "GET" in Eufy protos)
    get_request = encode_protobuf_field(1, 0, 0)  # field 1 = method, value 0
    get_b64 = base64.b64encode(encode_varint(len(get_request)) + get_request).decode()
    send_dps_command({"170": get_b64})
    send_dps_command({"171": get_b64})
    send_dps_command({"172": get_b64})
    print("  Sent protobuf GET requests on 170/171/172")

    print(f"\nWaiting {LISTEN_SECONDS}s for responses...\n")

    # Wait and listen
    try:
        await asyncio.sleep(LISTEN_SECONDS)
    except KeyboardInterrupt:
        print("\nInterrupted.")

    client.loop_stop()
    try:
        client.disconnect()
    except Exception:
        pass
    await api.close()

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary: received {message_count} messages in {LISTEN_SECONDS}s")
    if received_dps:
        print(f"DPS keys seen:")
        for k in sorted(received_dps.keys(), key=lambda x: int(x) if x.isdigit() else x):
            updates = received_dps[k]
            sizes = []
            for v in updates:
                if isinstance(v, str):
                    sizes.append(f"{len(v)}ch")
                else:
                    sizes.append(str(v))
            marker = " <-- MAP" if k in MAP_DPS_KEYS else ""
            print(f"  {k:>4s}: {len(updates)} update(s), values: {', '.join(sizes[:5])}{marker}")
    else:
        print("No DPS data received.")
    print()

    # Clean up temp files
    try:
        os.unlink(cert_file.name)
        os.unlink(key_file.name)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
