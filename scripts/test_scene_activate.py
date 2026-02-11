#!/usr/bin/env python3
"""
Test script: try different approaches to activate a cleaning scene.

Connects via MQTT, retrieves scene list, then tries sending the scene
activation command using different DPS targets and protobuf encodings.

Run from repo root:
  python scripts/test_scene_activate.py
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
    username, password = load_credentials()

    import types
    sys.modules["eufy_clean"] = types.ModuleType("eufy_clean")
    sys.modules["eufy_clean.api"] = types.ModuleType("eufy_clean.api")
    _load_module("eufy_clean.const", COMPONENT_ROOT / "const.py")
    _load_module("eufy_clean.api.proto_utils", COMPONENT_ROOT / "api" / "proto_utils.py")
    _load_module("eufy_clean.api.eufy_api", COMPONENT_ROOT / "api" / "eufy_api.py")
    from eufy_clean.api.eufy_api import EufyCleanApi
    from eufy_clean.api.proto_utils import (
        decode_scene_list,
        decode_varint,
        decode_protobuf_field,
        encode_varint,
        encode_protobuf_field,
    )

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
    dps = device.get("dps", {})
    print(f"Device: {device.get('device_name', device_id)} ({device_model})")

    # Get scene list from DPS 180
    raw_180 = dps.get("180", "")
    scenes = decode_scene_list(raw_180) if raw_180 else []
    if not scenes:
        print("No scenes found on device.")
        await api.close()
        return

    print(f"\nScenes on device:")
    for i, s in enumerate(scenes):
        print(f"  [{i}] {s['name']} (id={s['scene_id']}, enabled={s['enabled']})")

    # Use the first enabled scene
    target_scene = next((s for s in scenes if s.get("enabled")), scenes[0])
    scene_id = target_scene["scene_id"]
    print(f"\nTarget scene: '{target_scene['name']}' (id={scene_id})")

    # Also check what DPS 182 currently holds
    raw_182 = dps.get("182", "")
    if raw_182:
        print(f"\nCurrent DPS 182 value ({len(raw_182)} chars):")
        try:
            data_182 = base64.b64decode(raw_182)
            print(f"  Hex: {data_182.hex()}")
            print(f"  Protobuf decode:")
            # Strip length prefix
            ln, pos_after = decode_varint(data_182, 0)
            if 0 < ln == len(data_182) - pos_after:
                data_182 = data_182[pos_after:]
            pos = 0
            while pos < len(data_182):
                fn, wt, v, pos = decode_protobuf_field(data_182, pos)
                if fn is None:
                    break
                if wt == 0:
                    print(f"    field {fn} (varint): {v}")
                elif wt == 2 and isinstance(v, bytes):
                    print(f"    field {fn} (bytes, {len(v)}B): {v.hex()}")
        except Exception as e:
            print(f"  Decode error: {e}")
    else:
        print("\nDPS 182: not present in REST data")

    # Set up MQTT
    import paho.mqtt.client as mqtt

    openudid = api.openudid
    user_id = mqtt_creds.get("user_id", "")
    app_name = mqtt_creds.get("app_name", "eufy_home")
    client_id = f"android-{app_name}-eufy_android_{openudid}_{user_id}"

    topic_res = f"cmd/eufy_home/{device_model}/{device_id}/res"
    topic_smart = f"smart/mb/in/{device_id}"

    responses = []

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0 or str(reason_code) == "Success":
            print(f"\nConnected to MQTT broker")
            client.subscribe(topic_res)
            client.subscribe(topic_smart)
            client.subscribe(f"cmd/eufy_home/{device_model}/{device_id}/#")
        else:
            print(f"MQTT connection failed: rc={reason_code}")

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            data = payload.get("payload", {})
            if isinstance(data, str):
                data = json.loads(data)
            dps_data = data.get("data", {})
            if dps_data:
                responses.append(dps_data)
                print(f"  >> Response DPS keys: {sorted(dps_data.keys())}")
                for k, v in sorted(dps_data.items()):
                    if isinstance(v, str) and len(v) > 20:
                        print(f"     DPS {k}: str({len(v)} chars)")
                        # Try to decode work status from DPS 153
                        if k == "153":
                            from eufy_clean.api.proto_utils import decode_work_status
                            ws = decode_work_status(v)
                            print(f"       -> state={ws.get('state')}, mode={ws.get('mode')}")
                    else:
                        print(f"     DPS {k}: {v}")
        except Exception as e:
            print(f"  >> Parse error: {e}")

    def on_disconnect(client, userdata, flags, reason_code, properties):
        print(f"Disconnected: rc={reason_code}")

    mqttc = mqtt.Client(
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
        mqttc.tls_set(certfile=cert_file.name, keyfile=key_file.name)
        mqttc.tls_insecure_set(True)

    mqttc.on_connect = on_connect
    mqttc.on_message = on_message
    mqttc.on_disconnect = on_disconnect

    endpoint = mqtt_creds.get("endpoint_addr", "")
    print(f"Connecting to {endpoint}:8883 ...")
    mqttc.connect(endpoint, 8883, keepalive=60)
    mqttc.loop_start()
    await asyncio.sleep(3)

    msg_seq = [0]

    def send_dps(dps_data: dict, label: str) -> None:
        msg_seq[0] += 1
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
                "msg_seq": msg_seq[0],
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
        mqttc.publish(topic_req, json.dumps(mqtt_message))
        mqttc.publish(topic_out, json.dumps(mqtt_message))
        print(f"  Sent: {label}")
        print(f"    DPS: {dps_data}")

    # --- Prepare different scene command encodings ---

    # Encoding A: SceneRequest on DPS 182 — { field 1: { field 1: scene_id } }
    # (mirrors the scene structure in the scene list)
    inner_a = encode_protobuf_field(1, 0, scene_id)
    outer_a = encode_protobuf_field(1, 2, inner_a)
    cmd_a = base64.b64encode(encode_varint(len(outer_a)) + outer_a).decode()

    # Encoding B: SceneRequest on DPS 182 — { field 1: scene_id }
    msg_b = encode_protobuf_field(1, 0, scene_id)
    cmd_b = base64.b64encode(encode_varint(len(msg_b)) + msg_b).decode()

    # Encoding C: ModeCtrlRequest on DPS 152 — method=24, field 3 (generic) with scene_id
    scene_msg_c = encode_protobuf_field(1, 0, scene_id)
    msg_c = encode_protobuf_field(1, 0, 24) + encode_protobuf_field(3, 2, scene_msg_c)
    cmd_c = base64.b64encode(encode_varint(len(msg_c)) + msg_c).decode()

    # Encoding D: Direct scene_id as varint on DPS 182 (like BoostIQ uses a plain bool)
    # Not base64 — just the integer
    cmd_d_int = scene_id

    # Encoding E: ModeCtrlRequest on DPS 182 — method=24
    msg_e = encode_protobuf_field(1, 0, 24) + encode_protobuf_field(2, 2, inner_a)
    cmd_e = base64.b64encode(encode_varint(len(msg_e)) + msg_e).decode()

    approaches = [
        ("A", "182", cmd_a, "DPS 182: SceneRequest { f1: { f1: scene_id } }"),
        ("B", "182", cmd_b, "DPS 182: SceneRequest { f1: scene_id }"),
        ("C", "152", cmd_c, "DPS 152: ModeCtrlRequest method=24, f3: { f1: scene_id }"),
        ("D", "182", cmd_d_int, "DPS 182: raw integer scene_id"),
        ("E", "182", cmd_e, "DPS 182: method=24 + f2: { f1: scene_id }"),
    ]

    print(f"\n{'='*60}")
    print(f"Testing scene activation approaches")
    print(f"Scene: '{target_scene['name']}' (id={scene_id})")
    print(f"{'='*60}")

    for code, dps_key, cmd_value, description in approaches:
        responses.clear()
        print(f"\n--- Approach {code}: {description} ---")

        if isinstance(cmd_value, str):
            try:
                raw = base64.b64decode(cmd_value)
                print(f"  Payload hex: {raw.hex()}")
            except Exception:
                pass

        send_dps({dps_key: cmd_value}, description)

        # Wait for response
        print(f"  Waiting 8s for response...")
        await asyncio.sleep(8)

        if responses:
            print(f"  Got {len(responses)} response(s)")
            # Check if work status changed to cleaning/scene mode
            for r in responses:
                if "153" in r:
                    from eufy_clean.api.proto_utils import decode_work_status
                    ws = decode_work_status(r["153"])
                    state = ws.get("state", "?")
                    mode = ws.get("mode", "?")
                    print(f"  *** Work status: state={state}, mode={mode} ***")
                    if state == "cleaning" or mode == "scene":
                        print(f"\n  >>> SUCCESS! Approach {code} activated the scene! <<<")
                        # Stop the robot after success
                        from eufy_clean.api.proto_utils import encode_control_command, CONTROL_STOP_TASK
                        stop_cmd = encode_control_command(CONTROL_STOP_TASK)
                        await asyncio.sleep(2)
                        send_dps({"152": stop_cmd}, "STOP")
                        await asyncio.sleep(3)
                        break
        else:
            print(f"  No response received")

        # Brief pause between attempts
        await asyncio.sleep(2)

    print(f"\n{'='*60}")
    print("Done. Check which approach triggered the device.")

    mqttc.loop_stop()
    try:
        mqttc.disconnect()
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


if __name__ == "__main__":
    asyncio.run(main())
