#!/usr/bin/env python3
"""
Test script: brute-force different protobuf field numbers for scene activation.

The device responded to ModeCtrlRequest method=24 on DPS 152 but didn't
start cleaning. This script tries different field numbers for the scene
params to find the correct one.

Run from repo root:
  python scripts/test_scene_fields.py
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
        decode_work_status,
        decode_varint,
        decode_protobuf_field,
        encode_varint,
        encode_protobuf_field,
        encode_control_command,
        CONTROL_STOP_TASK,
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

    # Get scene list
    raw_180 = dps.get("180", "")
    scenes = decode_scene_list(raw_180) if raw_180 else []
    if not scenes:
        print("No scenes found.")
        await api.close()
        return

    target_scene = next((s for s in scenes if s.get("enabled")), scenes[0])
    scene_id = target_scene["scene_id"]
    print(f"Target scene: '{target_scene['name']}' (id={scene_id})")

    # Get current work status
    raw_153 = dps.get("153", "")
    if raw_153:
        ws = decode_work_status(raw_153)
        print(f"Current status: state={ws.get('state')}, mode={ws.get('mode')}")

    # Set up MQTT
    import paho.mqtt.client as mqtt

    openudid = api.openudid
    user_id = mqtt_creds.get("user_id", "")
    app_name = mqtt_creds.get("app_name", "eufy_home")
    client_id = f"android-{app_name}-eufy_android_{openudid}_{user_id}"

    topic_res = f"cmd/eufy_home/{device_model}/{device_id}/res"
    topic_smart = f"smart/mb/in/{device_id}"

    responses = []
    status_changed = asyncio.Event()

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0 or str(reason_code) == "Success":
            print(f"Connected to MQTT broker")
            client.subscribe(topic_res)
            client.subscribe(topic_smart)
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
                # Check for work status change
                if "153" in dps_data:
                    ws = decode_work_status(dps_data["153"])
                    state = ws.get("state", "?")
                    mode = ws.get("mode", "?")
                    print(f"    *** STATUS: state={state}, mode={mode} ***")
                    if state == "cleaning" or mode == "scene":
                        status_changed.set()
                elif "152" in dps_data:
                    raw = dps_data["152"]
                    try:
                        d = base64.b64decode(raw)
                        # Strip length prefix
                        ln, pafter = decode_varint(d, 0)
                        if 0 < ln == len(d) - pafter:
                            d = d[pafter:]
                        fields = []
                        p = 0
                        while p < len(d):
                            fn, wt, v, p = decode_protobuf_field(d, p)
                            if fn is None:
                                break
                            if wt == 0:
                                fields.append(f"f{fn}={v}")
                            elif wt == 2 and isinstance(v, bytes):
                                fields.append(f"f{fn}=msg({len(v)}B)")
                        print(f"    DPS 152 resp: {' '.join(fields)}")
                    except Exception:
                        print(f"    DPS 152 resp: {raw}")
                elif "177" in dps_data:
                    print(f"    DPS 177 (error update)")
        except Exception as e:
            pass

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

    def send_dps(dps_data: dict) -> None:
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

    def build_scene_cmd_field(field_num: int) -> str:
        """Build ModeCtrlRequest with method=24 and scene params on given field."""
        scene_msg = encode_protobuf_field(1, 0, scene_id)
        message = encode_protobuf_field(1, 0, 24)
        message += encode_protobuf_field(field_num, 2, scene_msg)
        return base64.b64encode(encode_varint(len(message)) + message).decode()

    def build_scene_cmd_no_params() -> str:
        """Build ModeCtrlRequest with method=24, no scene params."""
        message = encode_protobuf_field(1, 0, 24)
        return base64.b64encode(encode_varint(len(message)) + message).decode()

    # Also try: method=24 on DPS 152 combined with scene_id on DPS 182
    def build_scene_182(scene_id: int) -> str:
        """Build a simple scene request for DPS 182."""
        inner = encode_protobuf_field(1, 0, scene_id)
        outer = encode_protobuf_field(1, 2, inner)
        return base64.b64encode(encode_varint(len(outer)) + outer).decode()

    print(f"\n{'='*60}")
    print("Test 1: method=24 with NO params (just the method)")
    print(f"{'='*60}")
    responses.clear()
    cmd = build_scene_cmd_no_params()
    print(f"  Payload hex: {base64.b64decode(cmd).hex()}")
    send_dps({"152": cmd})
    await asyncio.sleep(6)
    print(f"  Responses: {len(responses)}")

    # Test field numbers 3-15, 27
    test_fields = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 27]

    for field_num in test_fields:
        responses.clear()
        status_changed.clear()
        print(f"\n{'='*60}")
        print(f"Test: method=24, scene params on field {field_num}")
        print(f"{'='*60}")
        cmd = build_scene_cmd_field(field_num)
        raw = base64.b64decode(cmd)
        print(f"  Payload hex: {raw.hex()}")
        send_dps({"152": cmd})

        # Wait for response
        try:
            await asyncio.wait_for(status_changed.wait(), timeout=6)
            print(f"  >>> DEVICE STARTED CLEANING! Field {field_num} is correct! <<<")
            # Stop the robot
            await asyncio.sleep(2)
            stop = encode_control_command(CONTROL_STOP_TASK)
            send_dps({"152": stop})
            await asyncio.sleep(3)
            break
        except asyncio.TimeoutError:
            print(f"  Responses: {len(responses)}, no status change")

        await asyncio.sleep(2)

    # Also try: send method=24 on DPS 152 AND scene_id on DPS 182 simultaneously
    print(f"\n{'='*60}")
    print("Test: DPS 152 method=24 + DPS 182 scene_id (simultaneous)")
    print(f"{'='*60}")
    responses.clear()
    status_changed.clear()
    cmd_152 = build_scene_cmd_no_params()
    cmd_182 = build_scene_182(scene_id)
    print(f"  DPS 152 hex: {base64.b64decode(cmd_152).hex()}")
    print(f"  DPS 182 hex: {base64.b64decode(cmd_182).hex()}")
    send_dps({"152": cmd_152, "182": cmd_182})
    try:
        await asyncio.wait_for(status_changed.wait(), timeout=8)
        print(f"  >>> DEVICE STARTED CLEANING! Dual DPS approach works! <<<")
        await asyncio.sleep(2)
        stop = encode_control_command(CONTROL_STOP_TASK)
        send_dps({"152": stop})
        await asyncio.sleep(3)
    except asyncio.TimeoutError:
        print(f"  Responses: {len(responses)}, no status change")

    print(f"\n{'='*60}")
    print("All tests complete.")

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
