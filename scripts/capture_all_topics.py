#!/usr/bin/env python3
"""
Subscribe to ALL MQTT topics for the device using wildcard subscriptions.
This will reveal if map/path data comes on a topic we're not monitoring.

SAFE: LISTEN-ONLY, no commands sent.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUTPUT_DIR = REPO_ROOT / "scripts" / "captured_data"
LISTEN_SECONDS = 300


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
    print("MQTT Wildcard Topic Capture (LISTEN-ONLY)")
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

    # Subscribe to ALL topics that might carry device data
    topics = [
        # Standard topics from the integration
        f"cmd/eufy_home/{device_model}/{device_id}/res",
        f"cmd/eufy_home/{device_model}/{device_id}/req",
        # Smart topics
        f"smart/mb/in/{device_id}",
        f"smart/mb/out/{device_id}",
        # Wildcard: all cmd topics for this device
        f"cmd/eufy_home/{device_model}/{device_id}/#",
        # Wildcard: all smart topics for this device
        f"smart/mb/+/{device_id}",
        f"smart/+/+/{device_id}",
        # Map-specific topics (speculative)
        f"map/eufy_home/{device_model}/{device_id}/#",
        f"data/eufy_home/{device_model}/{device_id}/#",
        f"stream/eufy_home/{device_model}/{device_id}/#",
        # Device-specific wildcards
        f"+/+/{device_model}/{device_id}/#",
        f"+/+/+/{device_id}",
        f"+/+/+/{device_id}/#",
    ]

    topic_messages: dict[str, list] = {}
    all_messages = []
    connected_event = asyncio.Event()

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0 or str(reason_code) == "Success":
            print(f"\n  Connected to MQTT broker")
            for t in topics:
                result = client.subscribe(t)
                print(f"  Subscribed: {t} (rc={result[0]})")
            print(f"\n  Listening for {LISTEN_SECONDS}s... Start cleaning now!")
            print(f"  (NO commands will be sent)\n")
            connected_event.set()
        else:
            print(f"  MQTT connect failed: {reason_code}")

    def on_message(client, userdata, msg):
        ts = time.time()
        topic = msg.topic
        payload_raw = msg.payload
        msg_num = len(all_messages) + 1

        topic_messages.setdefault(topic, []).append(ts)

        # Try to parse as JSON
        dps_keys = []
        has_map_data = False
        try:
            payload = json.loads(payload_raw.decode())
            data = payload.get("payload", {})
            if isinstance(data, str):
                data = json.loads(data)
            dps = data.get("data", {})
            if dps:
                dps_keys = sorted(dps.keys(), key=lambda x: int(x) if x.isdigit() else x)
                has_map_data = any(k in {"165", "166", "170", "171"} for k in dps_keys)

                # Save if it contains map/path data
                if has_map_data:
                    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                    ts_int = int(ts)
                    for k in dps_keys:
                        if k in {"165", "166", "170", "171"} and isinstance(dps[k], str) and len(dps[k]) > 4:
                            fname = OUTPUT_DIR / f"dps{k}_wildcard_{ts_int}.b64"
                            fname.write_text(dps[k])
                            raw = base64.b64decode(dps[k])
                            (OUTPUT_DIR / f"dps{k}_wildcard_{ts_int}.bin").write_bytes(raw)
                            print(f"  *** SAVED DPS {k}: {len(raw)} bytes")
        except Exception:
            pass

        # Determine if this is a new topic we haven't seen before
        is_new_topic = len(topic_messages[topic]) == 1

        marker = ">>>" if has_map_data else ("NEW" if is_new_topic else "   ")
        size = len(payload_raw)

        if dps_keys:
            print(f"  {marker} #{msg_num:>3d} [{topic}] DPS={dps_keys} ({size}B)")
        else:
            # Non-DPS message - show first 100 chars
            preview = payload_raw[:100].decode(errors="replace")
            print(f"  {marker} #{msg_num:>3d} [{topic}] ({size}B) {preview[:80]}")

        all_messages.append({
            "ts": ts,
            "topic": topic,
            "size": size,
            "dps_keys": dps_keys,
            "has_map_data": has_map_data,
        })

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

    try:
        await asyncio.wait_for(connected_event.wait(), timeout=10)
    except asyncio.TimeoutError:
        print("  Connection timeout.")
        client.loop_stop()
        await api.close()
        return

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

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Summary: {len(all_messages)} messages in {LISTEN_SECONDS}s")
    print(f"\nTopics seen:")
    for topic, timestamps in sorted(topic_messages.items()):
        print(f"  {len(timestamps):>4d} msgs  {topic}")

    map_msgs = [m for m in all_messages if m["has_map_data"]]
    if map_msgs:
        print(f"\n*** Found {len(map_msgs)} messages with map/path data (DPS 165/166/170/171)")
    else:
        print(f"\n  No map/path data (DPS 165/166/170/171) found on any topic.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
