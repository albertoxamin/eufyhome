#!/usr/bin/env python3
"""
Query DPS 171 (map list) from the device via MQTT.

This sends a SINGLE protobuf "get" request to DPS 171 ONLY,
then listens for the response. No other DPS keys are touched.

The old test_mqtt_map.py script that cleared the map:
  1. Sent raw \\x00 byte to DPS 171 (invalid protobuf → may reset)
  2. Also sent commands to DPS 170 and 172 simultaneously
This script avoids both of those mistakes.

Usage:
  python scripts/query_map_list.py
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUTPUT_DIR = REPO_ROOT / "scripts" / "captured_data"
LISTEN_SECONDS = 30


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


def decode_tree(data: bytes, depth: int = 0, max_depth: int = 5) -> list[dict]:
    from eufy_clean.api.proto_utils import decode_protobuf_field, decode_varint

    fields = []
    pos = 0
    while pos < len(data):
        fn, wt, val, pos = decode_protobuf_field(data, pos)
        if fn is None:
            break
        f = {"field": fn, "wt": wt}
        if wt == 0:
            f["val"] = val
        elif wt == 2:
            f["len"] = len(val)
            f["raw"] = val
            try:
                f["str"] = val.decode("utf-8")
            except Exception:
                pass
            if depth < max_depth and len(val) >= 2:
                try:
                    nested = decode_tree(val, depth + 1, max_depth)
                    if nested:
                        f["nested"] = nested
                except Exception:
                    pass
        fields.append(f)
    return fields


def show_tree(fields: list[dict], indent: int = 0) -> None:
    pfx = "  " * indent
    for f in fields:
        fn = f["field"]
        if f["wt"] == 0:
            print(f"{pfx}f{fn}: varint = {f['val']}")
        elif f["wt"] == 2:
            s = f.get("str")
            if "nested" in f and f["nested"]:
                print(f"{pfx}f{fn}: msg ({f['len']}B) {{")
                show_tree(f["nested"], indent + 1)
                print(f"{pfx}}}")
            elif s and s.isprintable() and len(s) > 0:
                print(f"{pfx}f{fn}: str = {s!r}")
            else:
                print(f"{pfx}f{fn}: bytes ({f['len']}B) = {f['raw'].hex()[:80]}")


async def main() -> None:
    import types

    username, password = load_credentials()

    sys.modules["eufy_clean"] = types.ModuleType("eufy_clean")
    sys.modules["eufy_clean.api"] = types.ModuleType("eufy_clean.api")
    _load_module("eufy_clean.const", COMPONENT_ROOT / "const.py")
    _load_module("eufy_clean.api.proto_utils", COMPONENT_ROOT / "api" / "proto_utils.py")
    _load_module("eufy_clean.api.eufy_api", COMPONENT_ROOT / "api" / "eufy_api.py")
    from eufy_clean.api.eufy_api import EufyCleanApi
    from eufy_clean.api.proto_utils import (
        decode_protobuf_field,
        decode_varint,
        encode_protobuf_field,
        encode_varint,
    )

    print("=" * 60)
    print("Query DPS 171 (map list) - SINGLE READ-ONLY REQUEST")
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
    print(f"Device: {device.get('device_name', device_id)} ({device_model})")

    mqtt_creds = api.mqtt_credentials
    if not mqtt_creds:
        print("No MQTT credentials.")
        await api.close()
        return

    import paho.mqtt.client as mqtt

    openudid = api.openudid
    user_id = mqtt_creds.get("user_id", "")
    app_name = mqtt_creds.get("app_name", "eufy_home")
    client_id = f"android-{app_name}-eufy_android_{openudid}_{user_id}"

    topic_req = f"cmd/eufy_home/{device_model}/{device_id}/req"
    topic_res = f"cmd/eufy_home/{device_model}/{device_id}/res"
    topic_smart = f"smart/mb/in/{device_id}"

    responses = []
    connected_event = asyncio.Event()

    def send_dps_command(client, dps_dict: dict) -> None:
        payload_inner = json.dumps({
            "data": dps_dict,
            "device_sn": device_id,
            "t": int(time.time() * 1000),
        })
        message = json.dumps({
            "head": {
                "client_id": client_id,
                "cmd": 65537,
                "timestamp": int(time.time() * 1000),
            },
            "payload": payload_inner,
        })
        client.publish(topic_req, message)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0 or str(reason_code) == "Success":
            print(f"  Connected to MQTT")
            client.subscribe(topic_res)
            client.subscribe(topic_smart)
            connected_event.set()
        else:
            print(f"  MQTT connect failed: {reason_code}")

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            data = payload.get("payload", {})
            if isinstance(data, str):
                data = json.loads(data)
            dps = data.get("data", {})
            if not dps:
                return

            dps_keys = sorted(dps.keys(), key=lambda x: int(x) if x.isdigit() else x)
            print(f"\n  Response DPS keys: {dps_keys}")

            for k in dps_keys:
                v = dps[k]
                if v is None:
                    print(f"    DPS {k}: None")
                    continue

                responses.append((k, v))

                if isinstance(v, str) and len(v) > 4:
                    try:
                        raw = base64.b64decode(v)
                        print(f"    DPS {k}: {len(v)} chars b64 ({len(raw)}B)")
                        print(f"    Hex: {raw.hex()}")

                        # Save raw data
                        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                        ts = int(time.time())
                        safe_id = device_id[:32]
                        (OUTPUT_DIR / f"dps{k}_{safe_id}_query_{ts}.b64").write_text(v)
                        (OUTPUT_DIR / f"dps{k}_{safe_id}_query_{ts}.bin").write_bytes(raw)

                        # Try protobuf decode
                        # Strip length prefix if present
                        decode_data = raw
                        if len(raw) >= 2:
                            ln, pa = decode_varint(raw, 0)
                            if 0 < ln <= len(raw) - pa and ln == len(raw) - pa:
                                decode_data = raw[pa:]

                        fields = decode_tree(decode_data)
                        if fields:
                            print(f"    Protobuf:")
                            show_tree(fields, indent=3)
                    except Exception as e:
                        print(f"    DPS {k}: {v!r:.100s} (decode err: {e})")
                else:
                    print(f"    DPS {k}: {v}")

        except Exception as e:
            print(f"  Parse error: {e}")

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )

    cert_pem = mqtt_creds.get("certificate_pem", "")
    private_key = mqtt_creds.get("private_key", "")
    cert_file = key_file = None
    if cert_pem and private_key:
        cert_file = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
        cert_file.write(cert_pem)
        cert_file.close()
        key_file = tempfile.NamedTemporaryFile(mode="w", suffix=".key", delete=False)
        key_file.write(private_key)
        key_file.close()
        client.tls_set(certfile=cert_file.name, keyfile=key_file.name)
        client.tls_insecure_set(True)

    client.on_connect = on_connect
    client.on_message = on_message

    endpoint = mqtt_creds.get("endpoint_addr", "")
    print(f"\n  Connecting to {endpoint}:8883 ...")
    client.connect(endpoint, 8883, keepalive=60)
    client.loop_start()

    # Wait for connection
    try:
        await asyncio.wait_for(connected_event.wait(), timeout=10)
    except asyncio.TimeoutError:
        print("  Connection timeout.")
        client.loop_stop()
        await api.close()
        return

    # Build the "get map list" protobuf request for DPS 171
    # Protobuf: field 1 (varint) = 0  → "get/query" method
    get_request = encode_protobuf_field(1, 0, 0)
    get_b64 = base64.b64encode(
        encode_varint(len(get_request)) + get_request
    ).decode()

    print(f"\n  Sending DPS 171 query: {get_b64}")
    print(f"  Raw bytes: {(encode_varint(len(get_request)) + get_request).hex()}")
    print(f"  (protobuf: field 1 = varint 0, meaning 'get/query')")
    print(f"  Waiting {LISTEN_SECONDS}s for response...\n")

    send_dps_command(client, {"171": get_b64})

    await asyncio.sleep(LISTEN_SECONDS)

    client.loop_stop()
    try:
        client.disconnect()
    except Exception:
        pass
    await api.close()

    if cert_file:
        try:
            os.unlink(cert_file.name)
        except Exception:
            pass
    if key_file:
        try:
            os.unlink(key_file.name)
        except Exception:
            pass

    print(f"\n{'=' * 60}")
    if responses:
        print(f"Got {len(responses)} DPS response(s)")
    else:
        print("No responses received for DPS 171 query.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
