#!/usr/bin/env python3
"""Print MQTT drive commands while the official app moves the robot.

Does not print account ids or certificates. Run, then use manual control
in the Eufy app. Ctrl-C stops.

  python3 scripts/listen_remote.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import download_map_p2p as dl  # noqa: E402


def _topic_kind(topic: str) -> str:
    if topic.startswith("biz/"):
        return "biz"
    if "/req" in topic:
        return "cmd-req"
    if "/res" in topic:
        return "cmd-res"
    if topic.startswith("smart/mb/out/"):
        return "smart-out"
    if topic.startswith("smart/mb/in/"):
        return "smart-in"
    return "other"


def _preview(value) -> str:
    if isinstance(value, (int, float, bool)) or value is None:
        return repr(value)
    text = value if isinstance(value, str) else json.dumps(value)
    if len(text) > 120:
        text = text[:117] + "..."
    return text


def main() -> None:
    username, password = dl.load_credentials()
    dl.bootstrap_eufy_modules()
    from eufy_clean.api.proto_utils import decode_varint
    from eufy_clean.proto.cloud import control_pb2

    async def login():
        from eufy_clean.api.eufy_api import EufyCleanApi

        api = EufyCleanApi(username=username, password=password)
        await api.login()
        devices = await api.get_all_devices()
        return api, devices

    api, devices = asyncio.run(login())
    if not devices or not api.mqtt_credentials:
        print("No MQTT device.")
        return

    creds = api.mqtt_credentials
    device = devices[0]
    device_id = device["device_id"]
    device_model = device["device_model"]
    user_id = creds.get("user_id", "")
    app_name = creds.get("app_name", "eufy_home")
    client_id = f"android-{app_name}-eufy_android_{api.openudid}_{user_id}-listen"

    topics = [
        f"cmd/eufy_home/{device_model}/{device_id}/req",
        f"cmd/eufy_home/{device_model}/{device_id}/res",
        f"biz/eufy_home/{device_model}/{device_id}/res",
        f"smart/mb/out/{device_id}",
        f"smart/mb/in/{device_id}",
    ]

    import paho.mqtt.client as mqtt

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )
    cert_pem = creds.get("certificate_pem", "")
    private_key = creds.get("private_key", "")
    files = []
    if cert_pem and private_key:
        for suffix, body in ((".pem", cert_pem), (".key", private_key)):
            handle = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
            handle.write(body)
            handle.close()
            files.append(handle.name)
        client.tls_set(certfile=files[0], keyfile=files[1])
        client.tls_insecure_set(True)

    def decode_direction(value: str) -> str:
        try:
            raw = base64.b64decode(value)
            _length, pos = decode_varint(raw, 0)
            msg = control_pb2.RemoteCtrl()
            msg.ParseFromString(raw[pos:])
            if msg.ByteSize():
                return f" RemoteCtrl.direction={msg.direction}"
            mode = control_pb2.ModeCtrlRequest()
            mode.ParseFromString(raw[pos:])
            if mode.ByteSize():
                return f" ModeCtrl.method={mode.method}"
        except Exception:
            return ""
        return ""

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"connected ({reason_code})")
        for topic in topics:
            client.subscribe(topic)
        print("listening. Use manual control in the Eufy app. Ctrl-C stops.")

    def on_message(client, userdata, msg):
        kind = _topic_kind(msg.topic)
        if kind == "biz":
            return
        try:
            body = json.loads(msg.payload.decode())
        except Exception:
            print(f"{kind} {len(msg.payload)} bytes")
            return
        payload = body.get("payload", body)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                print(f"{kind} payload {_preview(payload)}")
                return
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            print(f"{kind} {_preview(data)}")
            return
        interesting = {k: v for k, v in data.items() if str(k) in {"152", "155", "151", "3"}}
        shown = interesting or data
        parts = []
        for key, value in shown.items():
            extra = decode_direction(value) if isinstance(value, str) else ""
            parts.append(f"{key}={_preview(value)}{extra}")
        print(f"{time.strftime('%H:%M:%S')} {kind} " + " ".join(parts), flush=True)

    client.on_connect = on_connect
    client.on_message = on_message
    print("Connecting...")
    client.connect(creds.get("endpoint_addr", ""), 8883, keepalive=60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        for path in files:
            try:
                import os

                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
