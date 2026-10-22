#!/usr/bin/env python3
"""
Download vacuum map via MQTT biz/ protocol-41 (P2P stream path).

Sends DPS 172 MAP_GET_ALL, subscribes to biz/eufy_home/.../res, and saves
the rendered PNG when map pixels arrive.

Run from repo root:
  python3.11 scripts/download_map_p2p.py
  python3.11 scripts/download_map_p2p.py --timeout 120 --output scripts/captured_data/map.png
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
OUTPUT_DIR = REPO_ROOT / "scripts" / "captured_data"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_map_source(path: Path, map_handler) -> None:
    """Save decoded pixels so the HTML preview can re-render this floor."""
    md = map_handler.map_data
    if md is None:
        return
    payload = {
        "width": md.width,
        "height": md.height,
        "origin_x": md.origin_x,
        "origin_y": md.origin_y,
        "resolution": md.resolution,
        "raw_pixels": base64.b64encode(md.raw_pixels).decode(),
        "room_pixels": base64.b64encode(md.room_pixels).decode() if md.room_pixels else None,
        "room_outline_width": md.room_outline_width,
        "room_outline_height": md.room_outline_height,
        "room_outline_origin_x": md.room_outline_origin_x,
        "room_outline_origin_y": md.room_outline_origin_y,
        "room_names": {str(k): v for k, v in md.room_names.items()},
        "virtual_walls": md.virtual_walls,
        "forbidden_zones": md.forbidden_zones,
        "ban_mop_zones": md.ban_mop_zones,
        "robot_pixel": map_handler._robot_pixel,
        "robot_trail": map_handler._robot_trail[-400:],
        "dock_pixel": map_handler._dock_pixel,
        "cleaning_path": map_handler._cleaning_path[-400:],
    }
    path.write_text(json.dumps(payload))


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
        print("Missing credentials in test_credentials.env or env vars.")
        sys.exit(1)
    return username, password


def bootstrap_eufy_modules() -> None:
    import types

    for pkg in ("eufy_clean", "eufy_clean.api", "eufy_clean.proto", "eufy_clean.proto.cloud"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    cloud_dir = COMPONENT_ROOT / "proto" / "cloud"
    sys.modules["eufy_clean.proto.cloud"].__path__ = [str(cloud_dir)]

    for pb2 in sorted(cloud_dir.glob("*_pb2.py")):
        mod_name = f"eufy_clean.proto.cloud.{pb2.stem}"
        if mod_name not in sys.modules:
            _load_module(mod_name, pb2)

    _load_module("eufy_clean.const", COMPONENT_ROOT / "const.py")
    _load_module("eufy_clean.api.proto_utils", COMPONENT_ROOT / "api" / "proto_utils.py")
    _load_module("eufy_clean.api.map_stream", COMPONENT_ROOT / "api" / "map_stream.py")
    _load_module("eufy_clean.api.map_commands", COMPONENT_ROOT / "api" / "map_commands.py")
    _load_module("eufy_clean.api.eufy_api", COMPONENT_ROOT / "api" / "eufy_api.py")


_LIVE_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Live map</title>
<style>
  body { margin: 0; background: #111; color: #eee; font: 14px/1.4 sans-serif; }
  img { display: block; max-width: 100%; height: auto; image-rendering: pixelated; }
  #status { padding: 8px 12px; }
</style>
<img id="map" alt="Live map">
<div id="status">waiting for the map</div>
<script>
async function tick() {
  const img = document.getElementById("map");
  const res = await fetch("/map.png?t=" + Date.now());
  if (res.ok) {
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const old = img.src;
    img.src = url;
    if (old.startsWith("blob:")) URL.revokeObjectURL(old);
  }
  const status = await fetch("/status");
  if (status.ok) document.getElementById("status").textContent = await status.text();
}
setInterval(tick, 1000);
tick();
</script>
"""


def serve_live_map(map_handler, port: int) -> None:
    """Serve the latest rendered map. The image refreshes once a second."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                body = _LIVE_PAGE.encode()
                kind = "text/html; charset=utf-8"
            elif path == "/status":
                body = map_handler.status_text().encode()
                kind = "text/plain; charset=utf-8"
            elif path == "/map.png":
                body = map_handler.map_image or b""
                if not body:
                    self.send_response(404)
                    self.end_headers()
                    return
                kind = "image/png"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Live map: http://127.0.0.1:{port}/")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Download map via MQTT P2P stream")
    parser.add_argument("--timeout", type=int, default=90, help="Seconds to wait for map")
    parser.add_argument(
        "--hold",
        type=int,
        default=0,
        help="Seconds to keep listening after the map arrives, so path frames can land",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "map_p2p.png",
        help="Output PNG path",
    )
    parser.add_argument("--map-id", type=int, default=0, help="Optional cloud map id for MAP_GET_ONE")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Keep the MQTT stream open and serve a page that refreshes the map",
    )
    parser.add_argument("--port", type=int, default=8766, help="Port for --live")
    parser.add_argument(
        "--rotation",
        type=int,
        default=90,
        choices=(0, 90, 180, 270),
        help="Clockwise rotation applied to the rendered PNG (default: 90)",
    )
    args = parser.parse_args()

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    username, password = load_credentials()
    bootstrap_eufy_modules()

    from eufy_clean.api.eufy_api import EufyCleanApi
    from eufy_clean.api.map_commands import build_map_get_all_command, build_map_get_one_command
    from eufy_clean.api.map_stream import (
        MapStreamHandler,
        _pose_on_map,
        parse_biz_protocol41,
        try_decode_as_dynamic_data,
        try_extract_metadata_channels,
        try_extract_path_points,
    )

    print("Logging in...")
    api = EufyCleanApi(username=username, password=password)
    await api.login()

    devices = await api.get_all_devices()
    if not devices:
        print("No devices found.")
        await api.close()
        return

    mqtt_creds = api.mqtt_credentials
    if not mqtt_creds:
        print("No MQTT credentials.")
        await api.close()
        return

    device = devices[0]
    device_id = device["device_id"]
    device_model = device["device_model"]
    print(f"Device: {device.get('device_name', device_id)} ({device_model})")

    import paho.mqtt.client as mqtt

    openudid = api.openudid
    user_id = mqtt_creds.get("user_id", "")
    app_name = mqtt_creds.get("app_name", "eufy_home")
    client_id = f"android-{app_name}-eufy_android_{openudid}_{user_id}-{int(time.time() * 1000)}"

    topic_cmd = f"cmd/eufy_home/{device_model}/{device_id}/res"
    topic_biz = f"biz/eufy_home/{device_model}/{device_id}/res"
    topic_req = f"cmd/eufy_home/{device_model}/{device_id}/req"
    topic_out = f"smart/mb/out/{device_id}"

    map_handler = MapStreamHandler(rotation=args.rotation)
    map_received = asyncio.Event()
    biz_count = 0
    last_status = ""
    if args.live:
        serve_live_map(map_handler, args.port)

    def send_dps_command(dps_data: dict) -> None:
        payload_inner = json.dumps(
            {
                "account_id": user_id,
                "data": dps_data,
                "device_sn": device_id,
                "protocol": 2,
                "t": int(time.time() * 1000),
            }
        )
        mqtt_message = {
            "head": {
                "client_id": client_id,
                "cmd": 65537,
                "cmd_status": 2,
                "msg_seq": 1,
                "seed": "",
                "sess_id": client_id,
                "sign_code": 0,
                "timestamp": int(time.time() * 1000),
                "version": "1.0.0.1",
            },
            "payload": payload_inner,
        }
        body = json.dumps(mqtt_message)
        client.publish(topic_req, body)
        client.publish(topic_out, body)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0 or str(reason_code) == "Success":
            print(f"Connected to {mqtt_creds.get('endpoint_addr')}")
            client.subscribe(topic_cmd)
            client.subscribe(topic_biz)
            print(f"Subscribed to:\n  {topic_cmd}\n  {topic_biz}")

            if args.map_id:
                cmd = build_map_get_one_command(args.map_id)
                print(f"Sending MAP_GET_ONE for map id {args.map_id}...")
            else:
                cmd = build_map_get_all_command()
                print("Sending MAP_GET_ALL on DPS 172...")
            send_dps_command(cmd)
        else:
            print(f"MQTT connect failed: {reason_code}")

    def on_message(client, userdata, msg):
        nonlocal biz_count, last_status
        try:
            _on_biz(msg)
        except Exception as exc:
            print(f"  frame error: {exc}")

    def _on_biz(msg) -> None:
        nonlocal biz_count, last_status
        if msg.topic == topic_biz:
            biz_count += 1
            parsed = parse_biz_protocol41(msg.payload)
            if parsed is not None and len(parsed[1]) < 800:
                channel_id, hex_data = parsed
                channels = try_extract_metadata_channels(hex_data)
                if channels:
                    print(f"    channels {channels}")
                pose = try_decode_as_dynamic_data(hex_data)
                path_pts = try_extract_path_points(hex_data)
                if pose:
                    md = map_handler.map_data
                    placed = _pose_on_map(md, pose[0], pose[1]) if md is not None else None
                    print(f"    pose {pose[0]},{pose[1]} -> {placed}")
            updated = map_handler.handle_biz_payload(msg.payload)
            if map_handler.map_data:
                md = map_handler.map_data
                zones = map_handler.restricted_zone_counts()
                zone_note = ""
                if map_handler.has_restricted_zones():
                    zone_note = (
                        f", zones={zones['forbidden_zones']} no-go/"
                        f"{zones['ban_mop_zones']} no-mop/"
                        f"{zones['virtual_walls']} walls"
                    )
                print(
                    f"  biz #{biz_count}: channel update, map {md.width}x{md.height} "
                    f"(rendered={'yes' if updated else 'no'}){zone_note}"
                )
            else:
                print(f"  biz #{biz_count}: {len(msg.payload)} bytes (no map yet)")
            if map_handler.has_restricted_zones() and not map_handler.map_data:
                zones = map_handler.restricted_zone_counts()
                print(
                    f"           restricted zones pending: "
                    f"{zones['forbidden_zones']} no-go, "
                    f"{zones['ban_mop_zones']} no-mop, "
                    f"{zones['virtual_walls']} walls"
                )
            if map_handler.map_image:
                map_received.set()
            status = map_handler.status_text() if map_handler.map_data else ""
            if status and status != last_status:
                last_status = status
                print(f"  {status}")

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
    print(f"Connecting to {endpoint}:8883 ...")
    client.connect(endpoint, 8883, keepalive=60)
    client.loop_start()

    if args.live:
        print("Live view stays up. Open the map in the Eufy app so pose frames arrive.")
    else:
        print(f"Waiting up to {args.timeout}s for map stream...")
        print("(Tip: open the map in the Eufy app or start cleaning to trigger the stream.)")

    deadline = time.monotonic() + args.timeout
    request_interval = 15.0
    last_request = time.monotonic()
    hold_until = None

    while args.live or time.monotonic() < deadline:
        if map_received.is_set() and hold_until is None and not args.live:
            hold_until = time.monotonic() + args.hold
            if args.hold:
                print(f"Map arrived. Listening {args.hold}s more for the live path...")
        if not args.live and hold_until is not None and time.monotonic() >= hold_until:
            break
        if not args.live and map_received.is_set() and args.hold == 0:
            break
        if time.monotonic() - last_request >= request_interval:
            cmd = (
                build_map_get_one_command(args.map_id)
                if args.map_id
                else build_map_get_all_command(seq=int(time.time()) % 100000)
            )
            send_dps_command(cmd)
            print("  Re-sent map request...")
            last_request = time.monotonic()
        await asyncio.sleep(0.5)

    if map_received.is_set():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(map_handler.map_image or b"")
        print(f"Saved map to {args.output} ({len(map_handler.map_image or b'')} bytes)")
        source = args.output.with_name("map_source.json")
        _write_map_source(source, map_handler)
        print(f"Saved map source to {source}")
    else:
        print("Timed out waiting for map pixels.")
        print(f"  biz/ messages received: {biz_count}")
        if map_handler.map_data:
            print(
                f"  Partial map metadata: {map_handler.map_data.width}x"
                f"{map_handler.map_data.height} (no renderable image)"
            )

    if map_handler.last_seen_maps:
        print("Discovered maps:")
        for mid, name in sorted(map_handler.last_seen_maps.items()):
            print(f"  {mid}: {name}")

    room_names = map_handler.get_room_names()
    if room_names:
        print(f"Rooms ({len(room_names)}):")
        for rid, name in sorted(room_names.items()):
            print(f"  {rid}: {name}")
    else:
        md = map_handler.map_data
        print("Rooms: none")
        if md:
            print(
                f"  map has room_pixels={bool(md.room_pixels)}, "
                f"room_names={bool(md.room_names)}"
            )

    md = map_handler.map_data
    if md:
        raw = md.raw_pixels
        pv0 = pv3 = 0
        for i in range(md.width * md.height):
            bp = i >> 2
            bit = (i & 3) * 2
            pv = (raw[bp] >> bit) & 3 if bp < len(raw) else 0
            if pv == 0:
                pv0 += 1
            elif pv == 3:
                pv3 += 1
        print(
            f"Path data: embedded pv0={pv0} pv3={pv3}, "
            f"streamed_path_pts={len(map_handler._cleaning_path)}, "
            f"robot_trail={len(map_handler._robot_trail)}, "
            f"robot_pixel={map_handler._robot_pixel}"
        )

    client.loop_stop()
    try:
        client.disconnect()
    except Exception:
        pass
    await api.close()

    for f in (cert_file, key_file):
        if f:
            try:
                os.unlink(f.name)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
