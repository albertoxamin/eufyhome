#!/usr/bin/env python3
"""Drive the vacuum from this terminal with the arrow keys.

Hold an arrow to move. Release it and the robot brakes. q quits.

  python3 scripts/drive_keys.py
"""

from __future__ import annotations

import asyncio
import json
import os
import select
import sys
import tempfile
import termios
import threading
import time
import tty

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import download_map_p2p as dl  # noqa: E402

LABELS = {
    "forward": "forward",
    "back": "back",
    "left": "left",
    "right": "right",
    "brake": "brake",
}
ARROWS = {
    "\x1b[A": "forward",
    "\x1b[B": "back",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "w": "forward",
    "s": "back",
    "d": "right",
    "a": "left",
}


def main() -> None:
    username, password = dl.load_credentials()
    dl.bootstrap_eufy_modules()
    from eufy_clean.api.proto_utils import encode_control_command

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
    client_id = f"android-{app_name}-eufy_android_{api.openudid}_{user_id}-drive"
    topic_req = f"cmd/eufy_home/{device_model}/{device_id}/req"
    topic_out = f"smart/mb/out/{device_id}"

    import paho.mqtt.client as mqtt

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
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

    connected = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            connected.set()

    client.on_connect = on_connect
    client.connect(creds.get("endpoint_addr", ""), 8883, keepalive=60)
    client.loop_start()
    if not connected.wait(15):
        print("MQTT did not connect.")
        client.loop_stop()
        return

    def publish(dps: dict) -> None:
        payload = json.dumps(
            {
                "account_id": user_id,
                "data": dps,
                "device_sn": device_id,
                "protocol": 2,
                "t": int(time.time() * 1000),
            }
        )
        message = json.dumps(
            {
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
                "payload": payload,
            }
        )
        client.publish(topic_req, message)
        client.publish(topic_out, message)

    state = {"dir": None, "last": 0.0, "rc": False, "stop": False}
    lock = threading.Lock()

    def send(direction: str, start: bool = False) -> None:
        if start and direction != "brake":
            publish({"152": encode_control_command(5)})
        publish({"155": {"forward": 1, "back": 2, "left": 3, "right": 4, "brake": 0}[direction]})
        if direction == "brake" and start:
            publish({"152": encode_control_command(16)})

    def pump() -> None:
        while True:
            time.sleep(0.2)
            with lock:
                if state["stop"]:
                    return
                direction = state["dir"]
                fresh = time.time() - state["last"] < 0.35
            if direction and fresh:
                send(direction)
            elif direction and not fresh:
                send("brake")
                with lock:
                    state["dir"] = None

    threading.Thread(target=pump, daemon=True).start()
    print(f"Connected to {device.get('device_name', device_model)}.")
    print("Hold an arrow key to drive. q quits.")

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            char = os.read(fd, 1).decode("latin1")
            if char == "\x1b":
                if select.select([fd], [], [], 0.05)[0]:
                    char += os.read(fd, 2).decode("latin1")
            if char in ("q", "\x03"):
                break
            direction = ARROWS.get(char)
            if not direction:
                continue
            with lock:
                start = not state["rc"]
                state["rc"] = True
                state["dir"] = direction
                state["last"] = time.time()
            send(direction, start=start)
            print(f"\r{LABELS[direction]:<8}", end="", flush=True)
    finally:
        with lock:
            state["stop"] = True
            state["dir"] = None
        send("brake", start=True)
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        client.loop_stop()
        client.disconnect()
        for path in files:
            try:
                os.unlink(path)
            except OSError:
                pass
        print("\nStopped.")


if __name__ == "__main__":
    main()
