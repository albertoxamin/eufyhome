#!/usr/bin/env python3
"""HTML preview of the camera map renderer.

Uses the same render_map_png as the camera entity, on a small sample floor.
Nothing here is written into Home Assistant until you copy the settings.

Run from the repo root:
    python scripts/map_preview.py
Then open http://127.0.0.1:8765
"""

from __future__ import annotations

import base64
import json
import sys
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT = REPO_ROOT / "custom_components" / "eufy_clean"


def _stub_package(name: str, path: Path) -> None:
    """Register a package without running eufy_clean/__init__.py (that imports HA)."""
    module = types.ModuleType(name)
    module.__path__ = [str(path)]  # type: ignore[attr-defined]
    module.__package__ = name
    sys.modules[name] = module


_stub_package("custom_components", REPO_ROOT / "custom_components")
_stub_package("custom_components.eufy_clean", COMPONENT)
_stub_package("custom_components.eufy_clean.api", COMPONENT / "api")
_stub_package("custom_components.eufy_clean.proto", COMPONENT / "proto")
_stub_package("custom_components.eufy_clean.proto.cloud", COMPONENT / "proto" / "cloud")

from custom_components.eufy_clean.api.map_stream import (  # noqa: E402
    MapData,
    render_map_png,
)

W, H = 96, 72
RES = 5

ROOM_DEFAULTS = [
    "646496",
    "6496c8",
    "96c882",
    "c8a082",
    "b48cc8",
    "c8be6e",
    "8cbec8",
    "c88296",
    "a0c8b4",
]


def _pack_2bpp(values: list[int]) -> bytes:
    raw = bytearray((len(values) + 3) // 4)
    for i, pv in enumerate(values):
        raw[i >> 2] |= (pv & 3) << ((i & 3) * 2)
    return bytes(raw)


def sample_map() -> tuple[MapData, tuple[int, int], list[tuple[int, int]], tuple[int, int]]:
    """A small floor: rooms, a hall, a cleaning trace, a no-go box, dock, robot."""
    pv = [0] * (W * H)
    room = [0] * (W * H)

    def fill(x0: int, y0: int, x1: int, y1: int, rid: int, value: int = 2) -> None:
        for y in range(y0, y1):
            for x in range(x0, x1):
                pv[y * W + x] = value
                room[y * W + x] = (rid << 2) | 0

    def wall_box(x0: int, y0: int, x1: int, y1: int) -> None:
        for x in range(x0, x1):
            for y in (y0, y1 - 1):
                pv[y * W + x] = 1
                room[y * W + x] = 62 << 2
        for y in range(y0, y1):
            for x in (x0, x1 - 1):
                pv[y * W + x] = 1
                room[y * W + x] = 62 << 2

    fill(6, 6, 42, 36, 1)
    fill(46, 6, 88, 30, 2)
    fill(46, 34, 88, 64, 3)
    fill(6, 40, 40, 64, 4)
    fill(42, 18, 46, 28, 0)  # doorway, outside the room mask
    fill(20, 36, 28, 40, 0)
    wall_box(4, 4, 90, 66)

    trace = []
    x, y = 12, 14
    for step in range(50):
        x = 12 + (step % 24)
        y = 14 + step // 6
        trace.append((x, y))
        pv[y * W + x] = 0
        room[y * W + x] = (1 << 2) | 1

    dock = (14, 56)
    robot = trace[-1]
    # World cm. origin is 0, one pixel is RES cm.
    forbid = [
        (60 * RES, 40 * RES),
        (78 * RES, 40 * RES),
        (78 * RES, 54 * RES),
        (60 * RES, 54 * RES),
    ]
    data = MapData(
        raw_pixels=_pack_2bpp(pv),
        width=W,
        height=H,
        origin_x=0,
        origin_y=0,
        resolution=RES,
        room_pixels=bytes(room),
        room_outline_width=W,
        room_outline_height=H,
        room_names={1: "Living", 2: "Kitchen", 3: "Bedroom", 4: "Bath"},
        forbidden_zones=[forbid],
    )
    return data, robot, trace, dock


ROBOT_ICON: bytes | None = None
SOURCE_PATH = REPO_ROOT / "scripts" / "captured_data" / "map_source.json"
SAVED_PNG = REPO_ROOT / "scripts" / "captured_data" / "map_p2p.png"

# Colors in scripts/captured_data/map_p2p.png, from the renderer that saved it.
_PNG_FLOOR = {
    (100, 150, 200): 1,
    (150, 200, 130): 2,
    (200, 160, 130): 3,
    (180, 140, 200): 4,
}
_PNG_TRACE = {
    (115, 165, 95): 2,
    (165, 125, 95): 3,
    (145, 105, 165): 4,
}


def floor_from_png(path: Path):
    """Turn the saved camera PNG back into pixels the renderer can recolor."""
    from PIL import Image

    image = Image.open(path).convert("RGB")
    width, height = image.size
    pv = [0] * (width * height)
    room = [0] * (width * height)
    pixels = list(image.getdata())
    for y in range(height):
        src_y = height - 1 - y
        for x in range(width):
            color = pixels[y * width + x]
            i = src_y * width + x
            rid = _PNG_FLOOR.get(color)
            if rid is not None:
                pv[i] = 2
                room[i] = rid << 2
                continue
            rid = _PNG_TRACE.get(color)
            if rid is not None:
                pv[i] = 2
                room[i] = rid << 2
                continue
            if color == (20, 20, 20):
                pv[i] = 1
                room[i] = 62 << 2
    centers: dict[int, list[int]] = {}
    for i, cell in enumerate(room):
        rid = cell >> 2
        if not 1 <= rid <= 31:
            continue
        bucket = centers.setdefault(rid, [0, 0, 0])
        bucket[0] += i % width
        bucket[1] += i // width
        bucket[2] += 1
    stops = [
        (vals[0] // vals[2], vals[1] // vals[2])
        for _, vals in sorted(centers.items(), key=lambda item: item[1][0] // item[1][2])
        if vals[2]
    ]
    trail: list[tuple[int, int]] = []
    for stop in stops:
        if not trail:
            trail.append(stop)
            continue
        x0, y0 = trail[-1]
        x1, y1 = stop
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for step in range(1, steps + 1):
            trail.append((x0 + (x1 - x0) * step // steps, y0 + (y1 - y0) * step // steps))
    robot = trail[-1] if trail else (width // 2, height // 2)
    data = MapData(
        raw_pixels=_pack_2bpp(pv),
        width=width,
        height=height,
        resolution=RES,
        room_pixels=bytes(room),
        room_outline_width=width,
        room_outline_height=height,
        room_names={1: "Room 1", 2: "Room 2", 3: "Room 3", 4: "Room 4"},
    )
    return data, robot, trail, None


def _pairs(points) -> list[tuple[int, int]]:
    return [(int(p[0]), int(p[1])) for p in points or []]


def load_saved_map():
    """Load the floor saved by scripts/download_map_p2p.py."""
    if not SOURCE_PATH.exists():
        return None
    raw = json.loads(SOURCE_PATH.read_text())
    room_b64 = raw.get("room_pixels")
    data = MapData(
        raw_pixels=base64.b64decode(raw["raw_pixels"]),
        width=int(raw["width"]),
        height=int(raw["height"]),
        origin_x=int(raw.get("origin_x", 0)),
        origin_y=int(raw.get("origin_y", 0)),
        resolution=int(raw.get("resolution") or 5),
        room_pixels=base64.b64decode(room_b64) if room_b64 else None,
        room_outline_width=int(raw.get("room_outline_width") or 0),
        room_outline_height=int(raw.get("room_outline_height") or 0),
        room_outline_origin_x=int(raw.get("room_outline_origin_x") or 0),
        room_outline_origin_y=int(raw.get("room_outline_origin_y") or 0),
        room_names={int(k): v for k, v in (raw.get("room_names") or {}).items()},
        virtual_walls=[
            (tuple(w[0]), tuple(w[1])) for w in raw.get("virtual_walls") or []
        ],
        forbidden_zones=[_pairs(zone) for zone in raw.get("forbidden_zones") or []],
        ban_mop_zones=[_pairs(zone) for zone in raw.get("ban_mop_zones") or []],
    )
    robot = raw.get("robot_pixel")
    dock = raw.get("dock_pixel")
    trail = _pairs(raw.get("robot_trail"))
    path = [
        (int(p[0]), int(p[1]), bool(p[2])) for p in raw.get("cleaning_path") or []
    ]
    return (
        data,
        tuple(robot) if robot else None,
        trail,
        tuple(dock) if dock else None,
        path,
    )


_loaded = load_saved_map()
if _loaded is None and SAVED_PNG.exists():
    MAP, ROBOT, TRAIL, DOCK = floor_from_png(SAVED_PNG)
    CLEANING_PATH = []
    MAP_LABEL = f"your saved floor {MAP.width}x{MAP.height}"
elif _loaded is None:
    _sample, _robot, _trail, _dock = sample_map()
    MAP, ROBOT, TRAIL, DOCK, CLEANING_PATH = _sample, _robot, _trail, _dock, []
    MAP_LABEL = "sample floor"
else:
    MAP, ROBOT, TRAIL, DOCK, CLEANING_PATH = _loaded
    MAP_LABEL = f"your map {MAP.width}x{MAP.height}"


def _rgb(text: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    text = text.strip().lstrip("#")
    if len(text) != 6:
        return fallback
    try:
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _flag(qs: dict[str, list[str]], key: str, default: bool = True) -> bool:
    raw = qs.get(key, ["1" if default else "0"])[0]
    return raw not in ("0", "false", "off")


def render_query(qs: dict[str, list[str]]) -> bytes:
    """Render one PNG from the preview query string."""
    pixel_colors = {
        0: _rgb(qs.get("c0", ["1e1e1e"])[0], (30, 30, 30)),
        1: _rgb(qs.get("c1", ["141414"])[0], (20, 20, 20)),
        2: _rgb(qs.get("c2", ["c8c8c8"])[0], (200, 200, 200)),
        3: _rgb(qs.get("c3", ["c8c8c8"])[0], (200, 200, 200)),
    }
    rooms = qs.get("rooms", [",".join(ROOM_DEFAULTS[1:])])[0].split(",")
    palette = [(45, 45, 45)]
    for i, hex_color in enumerate(rooms[:8]):
        palette.append(_rgb(hex_color, _rgb(ROOM_DEFAULTS[i + 1], (100, 150, 200))))
    while len(palette) < 9:
        palette.append(palette[-1])
    try:
        radius = float(qs.get("radius", ["1.9"])[0])
    except ValueError:
        radius = 1.9
    radius = min(max(radius, 0.2), 6.0)
    try:
        max_px = int(qs.get("max_px", ["512"])[0])
    except ValueError:
        max_px = 512
    max_px = min(max(max_px, 128), 1024)
    rotation = int(qs.get("rotation", ["0"])[0])
    if rotation not in (0, 90, 180, 270):
        rotation = 0
    robot_style = qs.get("robot", ["dot"])[0]
    if robot_style not in ("googly", "dot"):
        robot_style = "googly"
    status = qs.get("status", [""])[0] or None
    style = {
        "pixel_colors": pixel_colors,
        "room_palette": palette,
        "path_color": _rgb(qs.get("path", ["ffffff"])[0], (255, 255, 255)),
        "path_radius_scale": radius,
        "path_width": min(max(int(qs.get("path_width", ["2"])[0] or 2), 1), 8),
        "path_style": qs.get("path_style", ["segmented"])[0],
        "show_embedded_path": _flag(qs, "path_on"),
        "show_labels": _flag(qs, "labels"),
        "show_zones": _flag(qs, "zones"),
        "render_scale": min(max(float(qs.get("dpi", ["2"])[0] or 2), 1), 4),
        "label_color": _rgb(qs.get("label", ["1e1e1e"])[0], (30, 30, 30)),
        "label_bg": _rgb(qs.get("label_bg", ["ffffff"])[0], (255, 255, 255)),
        "label_size": min(max(int(qs.get("label_size", ["8"])[0] or 8), 6), 28),
    }
    if robot_style == "svg" and ROBOT_ICON:
        style["robot_icon"] = ROBOT_ICON
    return render_map_png(
        MAP,
        robot_pixel=ROBOT if _flag(qs, "robot_on") else None,
        robot_trail=TRAIL if _flag(qs, "trail") else None,
        cleaning_path=CLEANING_PATH if _flag(qs, "trail") else None,
        dock_pixel=DOCK if _flag(qs, "dock") else None,
        robot_status=status,
        max_px=max_px,
        robot_style=robot_style,
        rotation=rotation,
        style=style,
    )


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Map preview</title>
<style>
  body { margin: 0; font: 14px/1.4 ui-sans-serif, system-ui, sans-serif; background: #111; color: #eee; }
  main { display: grid; grid-template-columns: 280px 1fr; min-height: 100vh; }
  form { padding: 16px; border-right: 1px solid #333; }
  h1 { font-size: 16px; margin: 0 0 8px; }
  p { color: #aaa; margin: 0 0 12px; }
  label { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin: 6px 0; }
  fieldset { border: 1px solid #333; margin: 12px 0; }
  legend { padding: 0 4px; }
  .stage { padding: 16px; }
  img { width: min(100%, 640px); height: auto; background: #000; }
  button { margin-top: 8px; }
  textarea { width: 100%; height: 90px; margin-top: 8px; background: #1b1b1b; color: #ddd; }
</style>
</head>
<body>
<main>
  <form id="controls">
    <h1>Map preview</h1>
    <p>%%MAP_LABEL%%. Same renderer as the camera. Copy the settings when you like the look. They are not applied to Home Assistant yet.</p>
    <fieldset>
      <legend>Floor pixels</legend>
      <label>Unknown <input type="color" name="c0" value="#1e1e1e"></label>
      <label>Wall <input type="color" name="c1" value="#141414"></label>
      <label>Free <input type="color" name="c2" value="#c8c8c8"></label>
      <label>Cleaned <input type="color" name="c3" value="#c8c8c8"></label>
    </fieldset>
    <fieldset>
      <legend>Rooms</legend>
      <label>1 <input type="color" name="r1" value="#6496c8"></label>
      <label>2 <input type="color" name="r2" value="#96c882"></label>
      <label>3 <input type="color" name="r3" value="#c8a082"></label>
      <label>4 <input type="color" name="r4" value="#b48cc8"></label>
      <label>5 <input type="color" name="r5" value="#c8be6e"></label>
      <label>6 <input type="color" name="r6" value="#8cbec8"></label>
      <label>7 <input type="color" name="r7" value="#c88296"></label>
      <label>8 <input type="color" name="r8" value="#a0c8b4"></label>
    </fieldset>
    <fieldset>
      <legend>Path and robot</legend>
      <label>Path <input type="color" name="path" value="#ffffff"></label>
      <label>Line
        <select name="path_style">
          <option value="segmented" selected>segmented</option>
          <option value="dotted">dotted</option>
          <option value="solid">solid</option>
        </select>
      </label>
      <label>Width <input type="range" name="path_width" min="1" max="8" step="1" value="2"></label>
      <label>Dot size <input type="range" name="radius" min="0.2" max="4" step="0.1" value="1.9"></label>
      <label>Rotation
        <select name="rotation">
          <option>0</option><option>90</option><option>180</option><option>270</option>
        </select>
      </label>
      <label>Render × <input type="range" name="dpi" min="1" max="4" step="1" value="2"></label>
      <label>Robot
        <select name="robot">
          <option value="googly">googly</option>
          <option value="dot" selected>dot</option>
          <option value="svg">svg</option>
        </select>
      </label>
      <label>SVG <input type="file" name="svg" accept="image/svg+xml,.svg"></label>
      <label>Badge
        <select name="status">
          <option value="">none</option>
          <option>charging</option>
          <option>washing</option>
          <option>drying</option>
          <option>emptying</option>
        </select>
      </label>
      <label>Max px <input type="number" name="max_px" min="128" max="1024" value="512"></label>
    </fieldset>
    <fieldset>
      <legend>Room names</legend>
      <label>Text <input type="color" name="label" value="#1e1e1e"></label>
      <label>Plate <input type="color" name="label_bg" value="#ffffff"></label>
      <label>Size <input type="range" name="label_size" min="8" max="22" step="1" value="8"></label>
    </fieldset>
    <fieldset>
      <legend>Layers</legend>
      <label>Embedded path <input type="checkbox" name="path_on" checked></label>
      <label>Preview path <input type="checkbox" name="trail" checked></label>
      <label>Room names <input type="checkbox" name="labels" checked></label>
      <label>No-go zone <input type="checkbox" name="zones" checked></label>
      <label>Dock <input type="checkbox" name="dock" checked></label>
      <label>Robot <input type="checkbox" name="robot_on" checked></label>
    </fieldset>
    <button type="button" id="copy">Copy settings</button>
    <textarea id="json" readonly></textarea>
  </form>
  <div class="stage"><img id="map" alt="Rendered map"></div>
</main>
<script>
const form = document.getElementById("controls");
const img = document.getElementById("map");
const out = document.getElementById("json");
function settings() {
  const data = new FormData(form);
  const flag = (name) => form.elements[name].checked ? "1" : "0";
  const rooms = [1,2,3,4,5,6,7,8].map((n) => data.get("r" + n).slice(1)).join(",");
  const q = new URLSearchParams({
    c0: data.get("c0").slice(1), c1: data.get("c1").slice(1),
    c2: data.get("c2").slice(1), c3: data.get("c3").slice(1),
    rooms, path: data.get("path").slice(1),
    path_style: data.get("path_style"), path_width: data.get("path_width"),
    radius: data.get("radius"), rotation: data.get("rotation"),
    robot: data.get("robot"), status: data.get("status"),
    max_px: data.get("max_px"), dpi: data.get("dpi"),
    label: data.get("label").slice(1), label_bg: data.get("label_bg").slice(1),
    label_size: data.get("label_size"),
    path_on: flag("path_on"), trail: flag("trail"), labels: flag("labels"),
    zones: flag("zones"), dock: flag("dock"), robot_on: flag("robot_on"),
  });
  return q;
}
let timer = 0;
function refresh() {
  const q = settings();
  img.src = "/map.png?" + q.toString();
  out.value = JSON.stringify(Object.fromEntries(q.entries()), null, 2);
}
form.addEventListener("input", (event) => {
  if (event.target && event.target.name === "svg") return;
  clearTimeout(timer);
  timer = setTimeout(refresh, 150);
});
form.elements.svg.addEventListener("change", async () => {
  const file = form.elements.svg.files[0];
  if (!file) return;
  const blobUrl = URL.createObjectURL(file);
  const drawn = new Image();
  await new Promise((ok, bad) => { drawn.onload = ok; drawn.onerror = bad; drawn.src = blobUrl; });
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  canvas.getContext("2d").drawImage(drawn, 0, 0, 128, 128);
  const blob = await new Promise((ok) => canvas.toBlob(ok, "image/png"));
  await fetch("/robot.png", { method: "POST", body: blob });
  form.elements.robot.value = "svg";
  refresh();
});
document.getElementById("copy").onclick = () => navigator.clipboard.writeText(out.value);
refresh();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        global ROBOT_ICON
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        if urlparse(self.path).path == "/robot.png" and body[:8] == b"\x89PNG\r\n\x1a\n":
            ROBOT_ICON = body
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(400)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = PAGE.replace("%%MAP_LABEL%%", MAP_LABEL).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/map.png":
            try:
                png = render_query(parse_qs(urlparse(self.path).query))
            except Exception as err:
                msg = str(err).encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(msg)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    if "--check" in sys.argv:
        sample, robot, trail, dock = sample_map()
        png = render_map_png(sample, robot_pixel=robot, robot_trail=trail, dock_pixel=dock)
        assert png[:8] == b"\x89PNG\r\n\x1a\n", "renderer did not return a PNG"
        red = render_map_png(
            sample,
            robot_pixel=robot,
            robot_trail=trail,
            dock_pixel=dock,
            style={"path_color": (255, 0, 0)},
        )
        assert red != png, "path color did not change the image"
        print(f"ok {len(png)} bytes")
        return
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Open http://127.0.0.1:8765")
    server.serve_forever()


if __name__ == "__main__":
    main()
