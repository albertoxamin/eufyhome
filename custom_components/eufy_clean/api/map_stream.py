"""Parse biz/ MQTT protocol-41 map stream messages and render PNG."""
from __future__ import annotations

import base64
import colorsys
import io
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ..proto.cloud import stream_pb2
from .proto_utils import decode_varint

_LOGGER = logging.getLogger(__name__)

# 2bpp pixel value → RGB (fallback when RoomOutline not available)
_PIXEL_COLORS: dict[int, tuple[int, int, int]] = {
    0: (30, 30, 30),    # UNKNOWN / unexplored
    1: (20, 20, 20),    # OBSTACLE / wall
    2: (200, 200, 200), # FREE floor
    3: (200, 200, 200), # CLEANED / carpet — same as free floor so it blends in
}

# Orange overlay for live robot trail and visited-pixel highlights.
_PATH_OVERLAY: tuple[int, int, int] = (255, 140, 0)

def _rgb_option(
    value: Any, default: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Read a stored RGB color, which is a list of three bytes."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(int(part) for part in value)  # type: ignore[return-value]
    return default


def _clamped_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, low), high)


def render_settings_from_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build renderer settings from config-entry options.

    Missing keys use the preview defaults: white segmented path, width 2,
    dot robot, and 2x rendering.
    """
    options = options or {}
    room_defaults = (
        (100, 150, 200),
        (150, 200, 130),
        (200, 160, 130),
        (180, 140, 200),
        (200, 190, 110),
        (140, 190, 200),
        (200, 130, 150),
        (160, 200, 180),
    )
    palette: list[tuple[int, int, int]] = [(45, 45, 45)]
    for index, default in enumerate(room_defaults, start=1):
        palette.append(_rgb_option(options.get(f"r{index}"), default))
    status = options.get("status") or None
    if status == "none":
        status = None
    return {
        "rotation": int(options.get("map_rotation", 0)),
        "max_px": int(options.get("max_px", 512)),
        "robot_style": options.get("robot") or "dot",
        "robot_status": status,
        "show_robot": bool(options.get("robot_on", True)),
        "show_trail": bool(options.get("trail", True)),
        "show_dock": bool(options.get("dock", True)),
        "style": {
            "pixel_colors": {
                0: _rgb_option(options.get("c0"), (30, 30, 30)),
                1: _rgb_option(options.get("c1"), (20, 20, 20)),
                2: _rgb_option(options.get("c2"), (200, 200, 200)),
                3: _rgb_option(options.get("c3"), (200, 200, 200)),
            },
            "room_palette": palette,
            "path_color": _rgb_option(options.get("path"), (255, 255, 255)),
            "path_alpha": _clamped_float(options.get("path_alpha"), 0.5, 0.0, 1.0),
            "path_style": options.get("path_style") or "segmented",
            "path_width": int(options.get("path_width", 2)),
            "path_spacing": _clamped_float(options.get("path_spacing"), 1.0, 0.25, 8.0),
            "path_radius_scale": float(options.get("radius", 1.9)),
            "icon_scale": _clamped_float(options.get("icon_scale"), 0.5, 0.01, 2.0),
            "render_scale": int(options.get("dpi", 2)),
            "label_color": _rgb_option(options.get("label"), (30, 30, 30)),
            "label_bg": _rgb_option(options.get("label_bg"), (255, 255, 255)),
            "label_size": int(options.get("label_size", 8)),
            "show_embedded_path": bool(options.get("path_on", True)),
            "show_labels": bool(options.get("labels", True)),
            "show_zones": bool(options.get("zones", True)),
        },
    }

_MAX_TRAIL_POINTS = 8000
_PathPoint = tuple[int, int, bool, bool]

# Special room-mask ids from p2pdata.proto MapPixels encoding.
_ROOM_MASK_GAP = 61
_ROOM_MASK_OBSTACLE = 62


def _map_pixel_value(raw: bytes, width: int, x: int, y: int) -> int:
    """Return the 2bpp map pixel value at (x, y)."""
    idx = y * width + x
    byte_pos = idx >> 2
    bit_pos = (idx & 3) * 2
    if byte_pos >= len(raw):
        return 0
    return (raw[byte_pos] >> bit_pos) & 3


def _room_mask_at(
    map_data: MapData,
    px_x: int,
    py: int,
) -> tuple[int, int]:
    """Return (room_id, sub_type) at a map pixel from the room outline mask."""
    room_px = map_data.room_pixels
    if (
        room_px is None
        or not map_data.room_outline_width
        or not map_data.room_outline_height
    ):
        return 0, 0
    res = map_data.resolution or 5
    ro_w = map_data.room_outline_width
    ro_h = map_data.room_outline_height
    ro_dx = round((map_data.origin_x - map_data.room_outline_origin_x) / res)
    ro_dy = round((map_data.origin_y - map_data.room_outline_origin_y) / res)
    rx, ry = px_x - ro_dx, py - ro_dy
    if 0 <= rx < ro_w and 0 <= ry < ro_h:
        rpx = room_px[ry * ro_w + rx]
        return rpx >> 2, rpx & 3
    return 0, 0


def _visited_path_pixels(map_data: MapData) -> list[tuple[int, int]]:
    """Return map pixels that encode a cleaning trace in the base map pixels.

    Matches jeppesens/eufy-clean: inside a room partition, trace pixels are the
    darker pv values where sub_type != 0 and pv is not free/cleaned floor.
    """
    raw = map_data.raw_pixels
    width, height = map_data.width, map_data.height
    room_px = map_data.room_pixels
    points: list[tuple[int, int]] = []

    for py in range(height):
        for px_x in range(width):
            pv = _map_pixel_value(raw, width, px_x, py)
            if room_px is not None:
                rid, sub_type = _room_mask_at(map_data, px_x, py)
                if rid == _ROOM_MASK_OBSTACLE or pv == 1:
                    continue
                if rid > 0 and sub_type != 0 and pv not in (2, 3):
                    points.append((px_x, py))
                continue
            if pv != 0:
                continue
            touches_floor = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = px_x + dx, py + dy
                if 0 <= nx < width and 0 <= ny < height:
                    if _map_pixel_value(raw, width, nx, ny) in (2, 3):
                        touches_floor = True
                        break
            if touches_floor:
                points.append((px_x, py))
    return points


def _darker_room_color(rgb: tuple[int, int, int], value_scale: float = 0.70) -> tuple[int, int, int]:
    """Same hue and saturation as the room, with a lower HSV value."""
    red, green, blue = (channel / 255 for channel in rgb)
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value * value_scale)
    return tuple(int(round(channel * 255)) for channel in (red, green, blue))


def _is_valid_room_id(rid: int) -> bool:
    return 1 <= rid <= 31


def _floor_leaked_past_walls(
    raw: bytes,
    width: int,
    height: int,
    room_px: bytes | None,
    ro_w: int,
    ro_h: int,
    ro_dx: int,
    ro_dy: int,
) -> bytearray:
    """Free-floor pixels outside the walls, reachable from the map edge.

    Black wall pixels (and room floors) stop the walk, so a ray that slips
    past the wall into the margin is hidden and the rooms stay.
    """
    leaked = bytearray(width * height)
    seen = bytearray(width * height)
    stack: list[tuple[int, int]] = []

    def rid_at(x: int, y: int) -> int:
        if not room_px or not ro_w or not ro_h:
            return 0
        rx, ry = x - ro_dx, y - ro_dy
        if 0 <= rx < ro_w and 0 <= ry < ro_h:
            return room_px[ry * ro_w + rx] >> 2
        return 0

    def push(x: int, y: int) -> None:
        if not (0 <= x < width and 0 <= y < height):
            return
        i = y * width + x
        if seen[i]:
            return
        seen[i] = 1
        pv = _map_pixel_value(raw, width, x, y)
        rid = rid_at(x, y)
        if pv == 1 or rid == _ROOM_MASK_OBSTACLE or _is_valid_room_id(rid):
            return
        stack.append((x, y))
        if pv in (2, 3):
            leaked[i] = 1

    if width <= 0 or height <= 0:
        return leaked
    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)
    while stack:
        x, y = stack.pop()
        push(x + 1, y)
        push(x - 1, y)
        push(x, y + 1)
        push(x, y - 1)
    return leaked


def _pixel_color_for_room_mask(
    pv: int,
    rid: int,
    sub_type: int,
    palette_len: int,
    pixel_colors: dict[int, tuple[int, int, int]] | None = None,
    room_palette: list[tuple[int, int, int]] | None = None,
) -> tuple[int, int, int]:
    """Color a map pixel using the room partition mask (jeppesens/eufy-clean logic).

    Inside rooms: solid room colour for floor, darker map pixels for the trace.
    Outside rooms: raw map pixel colours so corridors still show the path.
    """
    colors = _PIXEL_COLORS if pixel_colors is None else pixel_colors
    palette = _ROOM_PALETTE if room_palette is None else room_palette
    palette_len = len(palette)
    if rid == _ROOM_MASK_OBSTACLE or pv == 1:
        return colors[1]
    if rid > 0:
        if sub_type == 0 or pv in (2, 3):
            return palette[1 + (rid - 1) % (palette_len - 1)]
        return colors.get(pv, (30, 30, 30))
    return colors.get(pv, (30, 30, 30))


# Room ID → RGB.  Index 0 = wall/outside (unused in combined render); 1-N cycle.
_ROOM_PALETTE: list[tuple[int, int, int]] = [
    (45, 45, 45),
    (100, 150, 200),
    (150, 200, 130),
    (200, 160, 130),
    (180, 140, 200),
    (200, 190, 110),
    (140, 190, 200),
    (200, 130, 150),
    (160, 200, 180),
]

# Room scene type → fallback label when room.name is empty
_ROOM_SCENE_NAMES: dict[int, str] = {
    1: "STUDY", 2: "BEDROOM", 3: "RESTROOM", 4: "KITCHEN",
    5: "LIVING RM", 6: "DINING RM", 7: "CORRIDOR",
}

# Robot status badge: (circle_color, dark_symbol_pixel_offsets_from_badge_centre)
_STATUS_BADGE: dict[str, tuple[tuple[int, int, int], list[tuple[int, int]]]] = {
    "charging": (
        (255, 240, 0),  # bright yellow
        # Compact Z-bolt sized for r=5 badge
        [
            (0,-3),(1,-3),
            (-1,-2),(0,-2),
            (-2,-1),(-1,-1),(0,-1),(1,-1),
            (0,0),(1,0),
            (-1,1),(0,1),
            (-1,2),(-2,2),
        ],
    ),
    "emptying": (
        (160, 160, 160),  # grey
        [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
    ),
    "drying": (
        (135, 206, 235),  # sky blue
        [(-1, -1), (0, -1), (-1, 0), (0, 0), (-1, 1), (0, 1)],
    ),
    "washing": (
        (65, 105, 225),  # royal blue
        [(0, -1), (-1, 0), (1, 0), (-1, 1), (1, 1), (0, 2)],
    ),
    "station": (
        (180, 100, 210),  # purple
        [(0, -1), (-1, 0), (0, 0), (1, 0), (0, 1)],
    ),
}

# Dock icon — solid house pixel offsets (dx, dy) relative to dock centre.
_HOUSE_FILL: frozenset[tuple[int, int]] = frozenset([
    (0,-4),
    (-1,-3),(0,-3),(1,-3),
    (-2,-2),(-1,-2),(0,-2),(1,-2),(2,-2),
    (-3,-1),(-2,-1),(-1,-1),(0,-1),(1,-1),(2,-1),(3,-1),
    (-3,0),(-2,0),(-1,0),(0,0),(1,0),(2,0),(3,0),
    (-3,1),(-2,1),(-1,1),(0,1),(1,1),(2,1),(3,1),
    (-3,2),(-2,2),(2,2),(3,2),
    (-3,3),(-2,3),(2,3),(3,3),
])
_HOUSE_DOOR: tuple[tuple[int, int], ...] = (
    (-1,2),(0,2),(1,2),(-1,3),(0,3),(1,3),
)

_MAX_PNG_PX = 512


@dataclass
class MapData:
    """Decoded map pixel data from a Map or MapBackup proto."""
    raw_pixels: bytes
    width: int
    height: int
    origin_x: int = 0
    origin_y: int = 0
    resolution: int = 5
    room_pixels: bytes | None = field(default=None, repr=False)
    room_outline_width: int = 0
    room_outline_height: int = 0
    room_outline_origin_x: int = 0
    room_outline_origin_y: int = 0
    room_names: dict[int, str] = field(default_factory=dict)
    virtual_walls: list[tuple[tuple[int, int], tuple[int, int]]] = field(default_factory=list)
    forbidden_zones: list[list[tuple[int, int]]] = field(default_factory=list)
    ban_mop_zones: list[list[tuple[int, int]]] = field(default_factory=list)
    dock_cm: tuple[int, int] | None = None
    map_id: int = 0
    releases: int = 0

    def has_restricted_zones(self) -> bool:
        """Return True when any no-go, no-mop, or virtual-wall data is present."""
        return bool(self.virtual_walls or self.forbidden_zones or self.ban_mop_zones)

@dataclass
class RestrictedZoneLayers:
    """No-go, no-mop, and virtual-wall geometry from RestrictedZone proto."""

    virtual_walls: list[tuple[tuple[int, int], tuple[int, int]]] = field(
        default_factory=list
    )
    forbidden_zones: list[list[tuple[int, int]]] = field(default_factory=list)
    ban_mop_zones: list[list[tuple[int, int]]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.virtual_walls or self.forbidden_zones or self.ban_mop_zones
        )


def _dock_cm_from_info(info: Any) -> tuple[int, int] | None:
    """First charger pose on a MapInfo, in the same centimetres as the robot."""
    for pose in info.docks:
        if pose.x or pose.y:
            return pose.x, pose.y
    for dock in info.docks_v2:
        pose = dock.pose
        if pose.x or pose.y:
            return pose.x, pose.y
    return None


def _parse_restricted_zone(rz: Any) -> RestrictedZoneLayers:
    layers = RestrictedZoneLayers()
    for wall in rz.virtual_walls:
        segment = ((wall.p0.x, wall.p0.y), (wall.p1.x, wall.p1.y))
        # A pose frame parses as a wall at the origin. That is not a wall.
        if segment == ((0, 0), (0, 0)):
            continue
        layers.virtual_walls.append(segment)
    for zone in rz.forbidden_zones:
        layers.forbidden_zones.append(_quad_points(zone))
    for zone in rz.ban_mop_zones:
        layers.ban_mop_zones.append(_quad_points(zone))
    return layers


def _apply_restricted_layers(
    map_data: MapData, layers: RestrictedZoneLayers
) -> None:
    map_data.virtual_walls = list(layers.virtual_walls)
    map_data.forbidden_zones = list(layers.forbidden_zones)
    map_data.ban_mop_zones = list(layers.ban_mop_zones)


# ---------------------------------------------------------------------------
# Low-level helpers (LZ4)
# ---------------------------------------------------------------------------

def _hex_to_proto_bytes(hex_data: str) -> bytes:
    raw = bytes.fromhex(hex_data)
    _, pos = decode_varint(raw, 0)
    return raw[pos:]


def _hex_proto_candidates(hex_data: str) -> list[bytes]:
    """Return possible protobuf payloads from a biz/ hex frame."""
    raw = bytes.fromhex(hex_data)
    candidates = [raw]
    stripped = _hex_to_proto_bytes(hex_data)
    if stripped and stripped not in candidates:
        candidates.append(stripped)
    return candidates


def _room_ids_from_pixels(pixels: bytes) -> list[int]:
    """Return sorted room ids present in a room-outline mask."""
    ids = {byte >> 2 for byte in pixels}
    return sorted(rid for rid in ids if 1 <= rid <= 31)


def _lz4_block_decompress(data: bytes, uncompressed_size: int) -> bytes:
    output = bytearray()
    pos = 0
    n = len(data)
    while pos < n:
        token = data[pos]; pos += 1
        lit_len = (token >> 4) & 0xF
        if lit_len == 15:
            while pos < n:
                extra = data[pos]; pos += 1
                lit_len += extra
                if extra != 255:
                    break
        output.extend(data[pos: pos + lit_len]); pos += lit_len
        if pos >= n:
            break
        offset = data[pos] | (data[pos + 1] << 8); pos += 2
        match_len = (token & 0xF) + 4
        if (token & 0xF) == 15:
            while pos < n:
                extra = data[pos]; pos += 1
                match_len += extra
                if extra != 255:
                    break
        match_start = len(output) - offset
        for i in range(match_len):
            output.append(output[match_start + i])
    return bytes(output)


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _quad_points(q: Any) -> list[tuple[int, int]]:
    return [(q.p0.x, q.p0.y), (q.p1.x, q.p1.y), (q.p2.x, q.p2.y), (q.p3.x, q.p3.y)]


def _rotate_image(img: Image.Image, degrees: int) -> Image.Image:
    """Rotate the rendered map clockwise (0, 90, 180, or 270 degrees)."""
    degrees = int(degrees) % 360
    if degrees == 0:
        return img
    transpose = {
        90: Image.Transpose.ROTATE_270,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }.get(degrees)
    if transpose is None:
        _LOGGER.warning("Unsupported map rotation %s; using 0", degrees)
        return img
    return img.transpose(transpose)


def _bundled_icon(filename: str) -> Image.Image | None:
    """Load a map sprite shipped next to this component. Cached for live updates."""
    cache: dict[str, Image.Image | None] = getattr(_bundled_icon, "_cache", {})
    if filename not in cache:
        path = Path(__file__).resolve().parent.parent / "assets" / filename
        try:
            cache[filename] = Image.open(path).convert("RGBA")
        except OSError:
            cache[filename] = None
        _bundled_icon._cache = cache  # type: ignore[attr-defined]
    return cache[filename]


def _dashed_polygon(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color: tuple[int, int, int, int],
    dash: float,
    gap: float,
    width: int,
) -> None:
    """Stroke a closed polygon with a dashed outline."""
    if len(points) < 2:
        return
    loop = points + [points[0]]
    for (x0, y0), (x1, y1) in zip(loop, loop[1:]):
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist < 1:
            continue
        ux, uy = (x1 - x0) / dist, (y1 - y0) / dist
        pos = 0.0
        while pos < dist:
            run = min(dash, dist - pos)
            draw.line(
                [(x0 + ux * pos, y0 + uy * pos), (x0 + ux * (pos + run), y0 + uy * (pos + run))],
                fill=color,
                width=width,
            )
            pos += dash + gap


def _tint_icon(icon: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    """Keep the sprite's shape and paint it in one solid color."""
    tinted = Image.new("RGBA", icon.size, (*rgb, 255))
    tinted.putalpha(icon.getchannel("A"))
    return tinted


def _blit_icon(img: Image.Image, icon: Image.Image, cx: int, cy: int, width: int) -> Image.Image:
    """Paste an RGBA sprite centered on a map pixel. Width is in output pixels."""
    width = max(1, width)
    height = max(1, round(width * icon.height / icon.width))
    glyph = icon.resize((width, height), Image.Resampling.LANCZOS)
    base = img.convert("RGBA")
    base.paste(glyph, (cx - width // 2, cy - height // 2), glyph)
    return base.convert("RGB")


def _content_crop_box(
    img: Image.Image, background: tuple[int, int, int], margin: int = 16
) -> tuple[int, int, int, int]:
    """Box that keeps ``margin`` pixels past non-background pixels."""
    width, height = img.size
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for index, pixel in enumerate(img.getdata()):
        if pixel == background:
            continue
        x, y = index % width, index // width
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
    if max_x < 0:
        return 0, 0, width, height
    return (
        max(0, min_x - margin),
        max(0, min_y - margin),
        min(width, max_x + margin + 1),
        min(height, max_y + margin + 1),
    )


def _crop_to_content(
    img: Image.Image, background: tuple[int, int, int], margin: int = 16
) -> Image.Image:
    """Trim empty margin so at most ``margin`` pixels remain past the rooms."""
    return img.crop(_content_crop_box(img, background, margin))


def _rotated_point(x: int, y: int, width: int, height: int, degrees: int) -> tuple[int, int]:
    """Map a point through the same clockwise turn as ``_rotate_image``."""
    degrees = int(degrees) % 360
    if degrees == 90:
        return height - 1 - y, x
    if degrees == 180:
        return width - 1 - x, height - 1 - y
    if degrees == 270:
        return y, width - 1 - x
    return x, y


def _unrotated_point(x: int, y: int, width: int, height: int, degrees: int) -> tuple[int, int]:
    """Inverse of ``_rotated_point``."""
    degrees = int(degrees) % 360
    if degrees == 90:
        return y, height - 1 - x
    if degrees == 180:
        return width - 1 - x, height - 1 - y
    if degrees == 270:
        return width - 1 - y, x
    return x, y


@dataclass(frozen=True)
class MapFrame:
    """How a rendered PNG maps back to centimetres on the floor."""

    scale: float
    rotation: int
    pre_w: int
    pre_h: int
    crop_left: int
    crop_top: int
    image_w: int
    image_h: int
    origin_x: int
    origin_y: int
    resolution: int
    src_w: int
    src_h: int


def world_to_picture(frame: MapFrame, wx: int, wy: int) -> tuple[float, float]:
    """Map centimetres to a fraction of the cropped picture, top-left origin."""
    res = frame.resolution or 5
    mx = round((int(wx) - frame.origin_x) / res)
    my = round((int(wy) - frame.origin_y) / res)
    sx = round(mx * frame.scale)
    sy = round((frame.src_h - 1 - my) * frame.scale)
    ux, uy = _rotated_point(sx, sy, frame.pre_w, frame.pre_h, frame.rotation)
    if frame.image_w <= 0 or frame.image_h <= 0:
        return 0.0, 0.0
    return (ux - frame.crop_left) / frame.image_w, (uy - frame.crop_top) / frame.image_h


def picture_to_source(frame: MapFrame, nx: float, ny: float) -> tuple[int, int]:
    """Picture fraction to a source-map pixel."""
    fx = float(nx) * frame.image_w
    fy = float(ny) * frame.image_h
    ux = int(round(fx)) + frame.crop_left
    uy = int(round(fy)) + frame.crop_top
    sx, sy = _unrotated_point(ux, uy, frame.pre_w, frame.pre_h, frame.rotation)
    scale = frame.scale or 1.0
    mx = int(round(sx / scale))
    my = int(round(frame.src_h - 1 - (sy / scale)))
    return mx, my


def picture_to_world(frame: MapFrame, nx: float, ny: float) -> tuple[int, int]:
    """Picture fraction to centimetres on the floor."""
    mx, my = picture_to_source(frame, nx, ny)
    res = frame.resolution or 5
    return frame.origin_x + mx * res, frame.origin_y + my * res


def picture_rect_to_quad(
    frame: MapFrame, x1: float, y1: float, x2: float, y2: float
) -> list[tuple[int, int]]:
    """Axis-aligned picture rectangle to a quadrangle in centimetres."""
    points = [
        picture_to_world(frame, x1, y1),
        picture_to_world(frame, x2, y1),
        picture_to_world(frame, x2, y2),
        picture_to_world(frame, x1, y2),
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]


def picture_room_id(map_data: MapData, frame: MapFrame, nx: float, ny: float) -> int:
    """Room id under a point on the rendered picture. 0 is no room."""
    if not map_data.room_pixels or not map_data.room_outline_width or not map_data.room_outline_height:
        return 0
    mx, my = picture_to_source(frame, nx, ny)
    res = map_data.resolution or 5
    ro_dx = round((map_data.origin_x - map_data.room_outline_origin_x) / res)
    ro_dy = round((map_data.origin_y - map_data.room_outline_origin_y) / res)
    rx, ry = mx - ro_dx, my - ro_dy
    if 0 <= rx < map_data.room_outline_width and 0 <= ry < map_data.room_outline_height:
        idx = ry * map_data.room_outline_width + rx
        if 0 <= idx < len(map_data.room_pixels):
            return map_data.room_pixels[idx] >> 2
    return 0


def picture_zone_boxes(map_data: MapData, frame: MapFrame) -> dict[str, list[dict[str, float]]]:
    """No-go and no-mop zones as boxes on the rendered picture."""

    def boxes(quads: list[list[tuple[int, int]]]) -> list[dict[str, float]]:
        out: list[dict[str, float]] = []
        for index, quad in enumerate(quads):
            pts = [world_to_picture(frame, x, y) for x, y in quad]
            out.append(
                {
                    "i": index,
                    "x1": round(min(point[0] for point in pts), 4),
                    "y1": round(min(point[1] for point in pts), 4),
                    "x2": round(max(point[0] for point in pts), 4),
                    "y2": round(max(point[1] for point in pts), 4),
                }
            )
        return out

    return {
        "nogo": boxes(map_data.forbidden_zones),
        "nomop": boxes(map_data.ban_mop_zones),
    }


def picture_hit(map_data: MapData, frame: MapFrame, nx: float, ny: float) -> dict[str, Any]:
    """Room and zone under one picture point."""
    room_id = picture_room_id(map_data, frame, nx, ny)
    kind = ""
    index = -1
    best: tuple[float, str, int] | None = None
    for zone_kind, quads in (
        ("nogo", map_data.forbidden_zones),
        ("nomop", map_data.ban_mop_zones),
    ):
        for zone_index, quad in enumerate(quads):
            pts = [world_to_picture(frame, x, y) for x, y in quad]
            x1 = min(point[0] for point in pts)
            x2 = max(point[0] for point in pts)
            y1 = min(point[1] for point in pts)
            y2 = max(point[1] for point in pts)
            if x1 <= nx <= x2 and y1 <= ny <= y2:
                area = (x2 - x1) * (y2 - y1)
                if best is None or area < best[0]:
                    best = (area, zone_kind, zone_index)
    if best is not None:
        kind, index = best[1], best[2]
    return {
        "room_id": room_id,
        "room_name": map_data.room_names.get(room_id, ""),
        "zone_kind": kind,
        "zone_index": index,
    }


def _decode_room_pixels(pixels: bytes, pixel_size: int) -> bytes:
    if len(pixels) != pixel_size:
        return _lz4_block_decompress(pixels, pixel_size)
    return pixels


def _room_names_from_params(rp: Any) -> dict[int, str]:
    names: dict[int, str] = {}
    for room in rp.rooms:
        name = room.name.strip()
        if not name:
            name = _ROOM_SCENE_NAMES.get(room.scene.type, f"ROOM {room.id}")
        names[room.id] = name
    return names


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_map_png(
    map_data: MapData,
    robot_pixel: tuple[int, int] | None = None,
    robot_trail: list[tuple[int, int]] | None = None,
    cleaning_path: list[_PathPoint] | None = None,
    dock_pixel: tuple[int, int] | None = None,
    robot_status: str | None = None,
    max_px: int = _MAX_PNG_PX,
    robot_style: str = "googly",
    rotation: int = 0,
    style: dict[str, Any] | None = None,
    frame_out: list[MapFrame] | None = None,
) -> bytes:
    """Render a PNG from MapData using Pillow.

    Pipeline:
    1. Build flat color list from map pixels (room palette + lidar fallback).
    2. putdata() into PIL Image, Y-flip, LANCZOS scale.
    3. Draw restricted zones, room labels, dock icon, trail, robot marker.
    4. Encode to PNG bytes via img.save().
    """
    style = style or {}
    pixel_colors = style.get("pixel_colors") or _PIXEL_COLORS
    room_palette = style.get("room_palette") or _ROOM_PALETTE
    path_rgb = tuple(style["path_color"]) if style.get("path_color") else _PATH_OVERLAY
    path_alpha = int(round(_clamped_float(style.get("path_alpha"), 0.5, 0.0, 1.0) * 255))
    path_color = (path_rgb[0], path_rgb[1], path_rgb[2], path_alpha)
    path_radius_scale = float(style.get("path_radius_scale", 1.2))
    path_width = min(max(int(style.get("path_width") or 1), 1), 12)
    path_spacing = _clamped_float(style.get("path_spacing"), 1.0, 0.25, 8.0)
    icon_scale = _clamped_float(style.get("icon_scale"), 0.5, 0.01, 2.0)
    path_style = style.get("path_style") or "solid"
    if path_style not in ("solid", "dotted", "segmented"):
        path_style = "solid"
    show_embedded_path = bool(style.get("show_embedded_path", True))
    show_labels = bool(style.get("show_labels", True))
    show_zones = bool(style.get("show_zones", True))
    render_scale = float(style.get("render_scale") or 1)
    render_scale = min(max(render_scale, 1.0), 6.0)
    label_size = int(style.get("label_size") or 9)

    width, height = map_data.width, map_data.height
    if width * height > 4000 * 4000:
        raise ValueError(f"Map dimensions {width}x{height} exceed safety limit (max 4000x4000)")
    res = map_data.resolution or 5
    room_px = map_data.room_pixels

    # ------------------------------------------------------------------
    # Step 1 — build pixel color list + accumulate room centroids
    # ------------------------------------------------------------------
    _ro_w = _ro_h = _ro_dx = _ro_dy = 0
    raw = map_data.raw_pixels
    colors: list[tuple[int, int, int]] = []
    # rid → [sum_src_x, sum_src_y, count]  (source pixel space)
    src_centroids: dict[int, list[int]] = {}
    _has_room_names = bool(room_px is not None and map_data.room_outline_width and map_data.room_names)
    leaked_floor = _floor_leaked_past_walls(
        raw,
        width,
        height,
        room_px,
        map_data.room_outline_width,
        map_data.room_outline_height,
        round((map_data.origin_x - map_data.room_outline_origin_x) / res)
        if map_data.room_outline_width
        else 0,
        round((map_data.origin_y - map_data.room_outline_origin_y) / res)
        if map_data.room_outline_height
        else 0,
    )
    unknown = (pixel_colors or _PIXEL_COLORS)[0]

    if room_px is not None and map_data.room_outline_width and map_data.room_outline_height:
        _ro_w = map_data.room_outline_width
        _ro_h = map_data.room_outline_height
        _ro_dx = round((map_data.origin_x - map_data.room_outline_origin_x) / res)
        _ro_dy = round((map_data.origin_y - map_data.room_outline_origin_y) / res)
        palette_len = len(room_palette)
        for py in range(height):
            for px_x in range(width):
                i = py * width + px_x
                byte_pos = i >> 2
                bit_pos = (i & 3) * 2
                pv = (raw[byte_pos] >> bit_pos) & 3 if byte_pos < len(raw) else 0
                rx, ry = px_x - _ro_dx, py - _ro_dy
                if 0 <= rx < _ro_w and 0 <= ry < _ro_h:
                    rpx = room_px[ry * _ro_w + rx]
                    rid = rpx >> 2
                    sub_type = rpx & 3
                else:
                    rid = sub_type = 0
                color = _pixel_color_for_room_mask(
                    pv,
                    rid,
                    sub_type,
                    palette_len,
                    pixel_colors=pixel_colors,
                    room_palette=room_palette,
                )
                if leaked_floor[i]:
                    color = unknown
                colors.append(color)
                if _has_room_names and rid > 0 and rid in map_data.room_names:
                    if rid not in src_centroids:
                        src_centroids[rid] = [0, 0, 0, px_x, py, px_x, py]
                    src_centroids[rid][0] += px_x
                    src_centroids[rid][1] += py
                    src_centroids[rid][2] += 1
                    src_centroids[rid][3] = min(src_centroids[rid][3], px_x)
                    src_centroids[rid][4] = min(src_centroids[rid][4], py)
                    src_centroids[rid][5] = max(src_centroids[rid][5], px_x)
                    src_centroids[rid][6] = max(src_centroids[rid][6], py)
    else:
        for i in range(width * height):
            byte_pos = i >> 2
            bit_pos = (i & 3) * 2
            pv = (raw[byte_pos] >> bit_pos) & 3 if byte_pos < len(raw) else 0
            color = pixel_colors.get(pv, (30, 30, 30))
            if leaked_floor[i]:
                color = unknown
            colors.append(color)

    # ------------------------------------------------------------------
    # Step 2 — create PIL image, Y-flip, scale
    # ------------------------------------------------------------------
    img: Image.Image = Image.new("RGB", (width, height))
    img.putdata(colors)
    img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    fit = min(max_px / max(width, height), 1.0)
    scale = fit * render_scale
    out_w = max(1, round(width * scale))
    out_h = max(1, round(height * scale))
    if abs(scale - 1.0) > 1e-6:
        # Upscale with nearest so room pixels stay crisp; text is drawn after, at this size.
        resample = (
            Image.Resampling.NEAREST if render_scale > 1 else Image.Resampling.LANCZOS
        )
        img = img.resize((out_w, out_h), resample)

    draw = ImageDraw.Draw(img)

    # Map pixel → output pixel (Y-flip baked in)
    def _to_out(mx: int, my: int) -> tuple[int, int]:
        return round(mx * scale), round((height - 1 - my) * scale)

    # World cm → output pixel
    def _world_to_out(wx: int, wy: int) -> tuple[int, int]:
        return _to_out(
            round((wx - map_data.origin_x) / res),
            round((wy - map_data.origin_y) / res),
        )

    # Filled circle helper
    def _circle(cx: float, cy: float, r: float, color: tuple[int, int, int]) -> None:
        if r < 1.0:
            draw.point((round(cx), round(cy)), fill=color)
        else:
            draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=color)

    # ------------------------------------------------------------------
    # Step 3 — restricted zones
    # Filled at half alpha, dashed border, glyph placed after rotation.
    # ------------------------------------------------------------------
    _BAN_MOP_RGB = (255, 165, 0)
    _ZONE_RGB = (220, 50, 50)
    zone_marks: list[tuple[float, float, str, tuple[int, int, int]]] = []

    if show_zones:
        zone_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        zone_draw = ImageDraw.Draw(zone_layer)
        dash = max(4.0, 8 * render_scale)
        gap = max(3.0, 5 * render_scale)

        def _paint_zone(
            zones: list[list[tuple[int, int]]],
            rgb: tuple[int, int, int],
            glyph: str,
        ) -> None:
            for zone in zones:
                pts = [_world_to_out(p[0], p[1]) for p in zone]
                if len(pts) < 3:
                    continue
                zone_draw.polygon(pts, fill=(*rgb, 128))
                _dashed_polygon(zone_draw, pts, (*rgb, 255), dash, gap, max(1, round(render_scale)))
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                zone_marks.append((cx, cy, glyph, rgb))

        _paint_zone(map_data.ban_mop_zones, _BAN_MOP_RGB, "nomop.png")
        _paint_zone(map_data.forbidden_zones, _ZONE_RGB, "nogo.png")
        for wall in map_data.virtual_walls:
            draw.line(
                [_world_to_out(wall[0][0], wall[0][1]), _world_to_out(wall[1][0], wall[1][1])],
                fill=_ZONE_RGB,
            )
        img = Image.alpha_composite(img.convert("RGBA"), zone_layer).convert("RGB")
        draw = ImageDraw.Draw(img)

    # ------------------------------------------------------------------
    # Step 4 — cleaning trail / streamed path
    # Drawn on a transparent layer so the path color's alpha blends with the floor.
    # Dock, labels, and the robot are drawn after rotation so they stay upright.
    # ------------------------------------------------------------------
    path_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(path_layer)
    # About 1.5 m at 5 cm/pixel. Longer gaps are a new segment, not a line across the map.
    _MAX_TRAIL_JUMP_SQ = 30 * 30

    def _segment_style(travel: bool) -> str:
        # The app draws coverage solid, and return / go-to trips dotted.
        if path_style == "solid":
            return "solid"
        if path_style == "dotted":
            return "dotted"
        return "dotted" if travel else "solid"

    def _draw_path_segments(
        raw_pts: list[tuple[int, int]],
        breaks: list[bool] | None = None,
        travels: list[bool] | None = None,
    ) -> None:
        if not raw_pts:
            return
        out_pts = [_to_out(tx, ty) for tx, ty in raw_pts]
        # Width is output pixels, not multiplied by render scale, so a 3× image can stay skinny.
        stroke = path_width
        dot_step = max(1, round(max(stroke * 3, 6 * render_scale) * path_spacing))
        dash = max(1, round(max(stroke * 2, 10 * render_scale) * path_spacing))
        gap = max(1, round(max(stroke * 2, 7 * render_scale) * path_spacing))
        traveled = 0.0

        def _stroke(p0: tuple[float, float], p1: tuple[float, float], style: str) -> None:
            nonlocal traveled
            x0, y0 = p0
            x1, y1 = p1
            dist = math.hypot(x1 - x0, y1 - y0)
            if dist < 0.5:
                return
            if style == "solid":
                draw.line([p0, p1], fill=path_color, width=stroke)
                traveled += dist
                return
            ux, uy = (x1 - x0) / dist, (y1 - y0) / dist
            pos = 0.0
            if style == "dotted":
                while pos <= dist:
                    _circle(x0 + ux * pos, y0 + uy * pos, max(0.6, stroke / 2), path_color)
                    pos += dot_step
                traveled += dist
                return
            cycle = dash + gap
            while pos < dist:
                into = (traveled + pos) % cycle
                if into < dash:
                    run = min(dash - into, dist - pos)
                    if run <= 0:
                        pos += 1
                        continue
                    draw.line(
                        [(x0 + ux * pos, y0 + uy * pos), (x0 + ux * (pos + run), y0 + uy * (pos + run))],
                        fill=path_color,
                        width=stroke,
                    )
                    pos += run
                else:
                    skip = cycle - into
                    pos += skip if skip > 0 else 1
            traveled += dist

        if len(raw_pts) == 1:
            ox, oy = out_pts[0]
            _circle(ox, oy, max(0.6, stroke / 2), path_color)
            return
        for i in range(len(raw_pts) - 1):
            if breaks and i + 1 < len(breaks) and breaks[i + 1]:
                traveled = 0.0
                continue
            ddx = raw_pts[i + 1][0] - raw_pts[i][0]
            ddy = raw_pts[i + 1][1] - raw_pts[i][1]
            if ddx * ddx + ddy * ddy <= _MAX_TRAIL_JUMP_SQ:
                travel = bool(travels and i + 1 < len(travels) and travels[i + 1])
                _stroke(out_pts[i], out_pts[i + 1], _segment_style(travel))

    if cleaning_path:
        path_pixels: list[tuple[int, int]] = []
        path_breaks: list[bool] = []
        path_travel: list[bool] = []
        start_new = False
        for point in cleaning_path:
            x_cm, y_cm = point[0], point[1]
            break_before = bool(point[2]) if len(point) > 2 else False
            travel = bool(point[3]) if len(point) > 3 else False
            px = _pose_to_pixel(map_data, x_cm, y_cm)
            if px is None:
                start_new = True
                continue
            path_pixels.append(px)
            path_breaks.append(break_before or start_new)
            path_travel.append(travel)
            start_new = False
        _draw_path_segments(path_pixels, path_breaks, path_travel)

    if robot_trail:
        trail_pixels = [(point[0], point[1]) for point in robot_trail]
        trail_travel = [bool(point[2]) if len(point) > 2 else False for point in robot_trail]
        _draw_path_segments(trail_pixels, None, trail_travel)

    if show_embedded_path and map_data.raw_pixels:
        dot_r = max(1.0, scale * path_radius_scale)
        for px_x, py in _visited_path_pixels(map_data):
            ox, oy = _to_out(px_x, py)
            if 0 <= ox < out_w and 0 <= oy < out_h:
                _circle(ox, oy, dot_r, path_color)

    img = Image.alpha_composite(img.convert("RGBA"), path_layer).convert("RGB")
    draw = ImageDraw.Draw(img)

    # ------------------------------------------------------------------
    # Step 5 — rotate the floor, then draw upright labels and icons
    # ------------------------------------------------------------------
    pre_w, pre_h = img.size
    if rotation:
        img = _rotate_image(img, rotation)
    draw = ImageDraw.Draw(img)
    out_w, out_h = img.size

    def _upright(x: int, y: int) -> tuple[int, int]:
        return _rotated_point(x, y, pre_w, pre_h, rotation)

    for cx, cy, glyph_name, rgb in zone_marks:
        glyph = _bundled_icon(glyph_name)
        if glyph is None:
            continue
        mx, my = _upright(round(cx), round(cy))
        img = _blit_icon(
            img,
            _tint_icon(glyph, rgb),
            mx,
            my,
            max(8, round(16 * render_scale * icon_scale)),
        )
    if zone_marks:
        draw = ImageDraw.Draw(img)

    if dock_pixel is not None:
        dx, dy = _upright(*_to_out(dock_pixel[0], dock_pixel[1]))
        station = _bundled_icon("t2280_map_station.png")
        if station is not None:
            img = _blit_icon(
                img, station, dx, dy, max(1, round(36 * render_scale * icon_scale))
            )
            draw = ImageDraw.Draw(img)
        else:
            cell = max(1, round(render_scale))
            _house_border = {
                (nx, ny)
                for ox, oy in _HOUSE_FILL
                for nx, ny in ((ox + ddx, oy + ddy) for ddx in (-1, 0, 1) for ddy in (-1, 0, 1))
                if (nx, ny) not in _HOUSE_FILL
            }

            def _stamp(ox: int, oy: int, color: tuple[int, int, int]) -> None:
                bpx, bpy = dx + ox * cell, dy + oy * cell
                if 0 <= bpx < out_w and 0 <= bpy < out_h:
                    draw.rectangle(
                        [bpx, bpy, bpx + cell - 1, bpy + cell - 1],
                        fill=color,
                    )

            for ox, oy in _house_border:
                _stamp(ox, oy, (100, 75, 0))
            for ox, oy in _HOUSE_FILL:
                _stamp(ox, oy, (255, 215, 0))
            for ox, oy in _HOUSE_DOOR:
                _stamp(ox, oy, (100, 75, 0))

    if show_labels and src_centroids:
        label_px = max(6, round(label_size * render_scale * 0.85))
        try:
            font: ImageFont.ImageFont | ImageFont.FreeTypeFont = ImageFont.load_default(
                size=label_px
            )
        except TypeError:
            font = ImageFont.load_default()
        for rid, vals in src_centroids.items():
            if vals[2] == 0:
                continue
            label = map_data.room_names[rid]
            if not label:
                continue
            room_color = room_palette[1 + (rid - 1) % (len(room_palette) - 1)]
            ox, oy = _upright(*_to_out(vals[0] // vals[2], vals[1] // vals[2]))
            bbox = draw.textbbox((0, 0), label, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            corners = [
                _upright(*_to_out(vals[3], vals[4])),
                _upright(*_to_out(vals[5], vals[4])),
                _upright(*_to_out(vals[3], vals[6])),
                _upright(*_to_out(vals[5], vals[6])),
            ]
            room_w = max(point[0] for point in corners) - min(point[0] for point in corners)
            glyph = Image.new("RGBA", (max(1, text_w + 2), max(1, text_h + 2)), (0, 0, 0, 0))
            ImageDraw.Draw(glyph).text(
                (1 - bbox[0], 1 - bbox[1]),
                label,
                font=font,
                fill=_darker_room_color(room_color),
            )
            if text_w > room_w:
                glyph = glyph.rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
            base = img.convert("RGBA")
            base.paste(glyph, (ox - glyph.width // 2, oy - glyph.height // 2), glyph)
            img = base.convert("RGB")
            draw = ImageDraw.Draw(img)

    # ------------------------------------------------------------------
    # Step 7 — robot marker + status badge
    # ------------------------------------------------------------------
    if robot_pixel is not None:
        orx, ory = _upright(*_to_out(robot_pixel[0], robot_pixel[1]))
        icon = style.get("robot_icon")
        if robot_style == "svg" and icon:
            glyph = Image.open(io.BytesIO(icon)).convert("RGBA")
            side = max(12, round(28 * render_scale))
            glyph = glyph.resize((side, side), Image.Resampling.LANCZOS)
            base = img.convert("RGBA")
            base.paste(glyph, (orx - side // 2, ory - side // 2), glyph)
            img = base
            draw = ImageDraw.Draw(img)
        elif robot_style == "dot":
            robot_icon = _bundled_icon("t2280_map_robot.png")
            if robot_icon is not None:
                img = _blit_icon(
                    img, robot_icon, orx, ory, max(1, round(22 * render_scale * icon_scale))
                )
                draw = ImageDraw.Draw(img)
            else:
                _circle(orx, ory, 7.0 * render_scale, (255, 255, 255))
                _circle(orx, ory, 4.5 * render_scale, (20, 20, 20))
        else:  # "googly" (default)
            _circle(orx, ory, 7.0 * render_scale, (160, 70, 0))
            _circle(orx, ory, 6.0 * render_scale, (255, 140, 0))
            for ex, ey in ((-1, -1), (2, -1)):
                _circle(orx + ex * render_scale, ory + ey * render_scale, 1.5 * render_scale, (255, 255, 255))
                _circle(orx + ex * render_scale, ory + ey * render_scale, 0.6 * render_scale, (20, 20, 20))

        if robot_status and robot_status in _STATUS_BADGE:
            badge_color, icon_offsets = _STATUS_BADGE[robot_status]
            bx, by = orx + round(6 * render_scale), ory - round(6 * render_scale)
            _circle(bx, by, 6.0 * render_scale, (30, 30, 30))
            _circle(bx, by, 5.0 * render_scale, badge_color)
            for ox2, oy2 in icon_offsets:
                bpx = bx + round(ox2 * render_scale)
                bpy = by + round(oy2 * render_scale)
                _circle(bpx, bpy, max(0.6, 0.45 * render_scale), (20, 20, 20))

    crop_left = crop_top = 0
    if src_centroids:
        corners = []
        for vals in src_centroids.values():
            if len(vals) < 7 or vals[2] == 0:
                continue
            corners.extend(
                _upright(*_to_out(x, y))
                for x, y in (
                    (vals[3], vals[4]),
                    (vals[5], vals[4]),
                    (vals[3], vals[6]),
                    (vals[5], vals[6]),
                )
            )
        if corners:
            crop_left = max(0, min(point[0] for point in corners) - 16)
            crop_top = max(0, min(point[1] for point in corners) - 16)
            img = img.crop(
                (
                    crop_left,
                    crop_top,
                    min(img.width, max(point[0] for point in corners) + 17),
                    min(img.height, max(point[1] for point in corners) + 17),
                )
            )
    else:
        crop_left, crop_top, crop_right, crop_bottom = _content_crop_box(img, unknown)
        img = img.crop((crop_left, crop_top, crop_right, crop_bottom))
    if frame_out is not None:
        frame_out.append(
            MapFrame(
                scale=scale,
                rotation=int(rotation) % 360,
                pre_w=pre_w,
                pre_h=pre_h,
                crop_left=crop_left,
                crop_top=crop_top,
                image_w=img.width,
                image_h=img.height,
                origin_x=map_data.origin_x,
                origin_y=map_data.origin_y,
                resolution=res,
                src_w=width,
                src_h=height,
            )
        )
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=3)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Protocol parsing
# ---------------------------------------------------------------------------

def try_extract_map_data(hex_data: str) -> MapData | None:
    """Try to extract MapData from biz/ channel hex data.

    Attempts MapBackup first (map-edit snapshot), then plain Map (cleaning stream).
    """
    try:
        proto_bytes = _hex_to_proto_bytes(hex_data)
    except Exception:
        return None

    map_msg = None
    room_pixels: bytes | None = None
    ro_width = ro_height = ro_origin_x = ro_origin_y = 0
    room_names: dict[int, str] = {}
    virtual_walls: list[tuple[tuple[int, int], tuple[int, int]]] = []
    forbidden_zones: list[list[tuple[int, int]]] = []
    ban_mop_zones: list[list[tuple[int, int]]] = []

    try:
        backup = stream_pb2.MapBackup().FromString(proto_bytes)
        if backup.map.pixels and backup.map.pixel_size:
            map_msg = backup.map

            ro = backup.rooms
            if ro.pixels and ro.pixel_size and ro.width and ro.height:
                room_pixels = _decode_room_pixels(ro.pixels, ro.pixel_size)
                ro_width, ro_height = ro.width, ro.height
                ro_origin_x, ro_origin_y = ro.origin.x, ro.origin.y
                _LOGGER.debug("RoomOutline decoded: %dx%d origin=(%d,%d)", ro_width, ro_height, ro_origin_x, ro_origin_y)

            if backup.room_params.rooms:
                room_names = _room_names_from_params(backup.room_params)
                _LOGGER.debug("RoomParams room_names: %s", room_names)

            rz = backup.restricted_zone
            layers = _parse_restricted_zone(rz)
            virtual_walls = layers.virtual_walls
            forbidden_zones = layers.forbidden_zones
            ban_mop_zones = layers.ban_mop_zones

            _LOGGER.debug(
                "RestrictedZone: %d walls, %d forbidden, %d ban-mop",
                len(virtual_walls), len(forbidden_zones), len(ban_mop_zones),
            )
    except Exception:
        pass

    if map_msg is None:
        try:
            m = stream_pb2.Map().FromString(proto_bytes)
            if m.pixels and m.pixel_size:
                map_msg = m
        except Exception:
            pass

    if map_msg is None or not map_msg.info.width or not map_msg.info.height:
        return None

    raw = map_msg.pixels
    if len(raw) != map_msg.pixel_size:
        try:
            raw = _lz4_block_decompress(raw, map_msg.pixel_size)
        except Exception as exc:
            _LOGGER.debug("LZ4 decompress failed: %s", exc)
            return None

    _LOGGER.debug(
        "Map decoded: %dx%d id=%d res=%d origin=(%d,%d)",
        map_msg.info.width, map_msg.info.height, map_msg.id,
        map_msg.info.resolution, map_msg.info.origin.x, map_msg.info.origin.y,
    )

    return MapData(
        raw_pixels=raw,
        width=map_msg.info.width,
        height=map_msg.info.height,
        origin_x=map_msg.info.origin.x,
        origin_y=map_msg.info.origin.y,
        resolution=map_msg.info.resolution or 5,
        room_pixels=room_pixels,
        room_outline_width=ro_width,
        room_outline_height=ro_height,
        room_outline_origin_x=ro_origin_x,
        room_outline_origin_y=ro_origin_y,
        room_names=room_names,
        virtual_walls=virtual_walls,
        forbidden_zones=forbidden_zones,
        ban_mop_zones=ban_mop_zones,
        dock_cm=_dock_cm_from_info(map_msg.info),
        map_id=int(map_msg.map_id or map_msg.info.map_id or 0),
        releases=int(map_msg.releases or 0),
    )


def try_extract_room_outline(
    hex_data: str,
) -> tuple[bytes, int, int, int, int] | None:
    """Extract room color mask from a biz/ RoomOutline frame."""
    try:
        proto_bytes = _hex_to_proto_bytes(hex_data)
        ro = stream_pb2.RoomOutline().FromString(proto_bytes)
        if ro.pixels and ro.pixel_size and ro.width and ro.height:
            pixels = _decode_room_pixels(ro.pixels, ro.pixel_size)
            return pixels, ro.width, ro.height, ro.origin.x, ro.origin.y
    except Exception:
        pass
    return None


def try_extract_room_params(hex_data: str) -> dict[int, str] | None:
    """Extract room id → name mapping from a biz/ RoomParams frame."""
    from ..proto.cloud import stream_wrap_pb2

    for proto_bytes in _hex_proto_candidates(hex_data):
        try:
            rp = stream_pb2.RoomParams().FromString(proto_bytes)
            if rp.rooms:
                names = _room_names_from_params(rp)
                if names:
                    return names
        except Exception:
            pass
        try:
            wrap = stream_wrap_pb2.RoomParamsWrap().FromString(proto_bytes)
            names: dict[int, str] = {}
            for rp in wrap.room_params:
                names.update(_room_names_from_params(rp))
            if names:
                return names
        except Exception:
            pass
    return None


def try_extract_restricted_zone(hex_data: str) -> RestrictedZoneLayers | None:
    """Extract no-go / no-mop / virtual-wall data from a biz/ RestrictedZone frame."""
    try:
        proto_bytes = _hex_to_proto_bytes(hex_data)
        rz = stream_pb2.RestrictedZone().FromString(proto_bytes)
        layers = _parse_restricted_zone(rz)
        if layers.is_empty():
            return None
        _LOGGER.debug(
            "RestrictedZone frame: %d walls, %d forbidden, %d ban-mop",
            len(layers.virtual_walls),
            len(layers.forbidden_zones),
            len(layers.ban_mop_zones),
        )
        return layers
    except Exception:
        pass
    return None


def try_extract_map_description(hex_data: str) -> tuple[int, str] | None:
    """Extract ``(map_id, name)`` from a biz/ ``MapDescription`` frame, or None.

    Each saved map's id and friendly name arrive over the cloud map stream as a
    single-shot ``MapDescription`` when that map becomes active (e.g. after a map
    switch). Other small biz/ frames (robot pose ``DynamicData``, ``RoomParams``)
    can also decode without raising, so guard strictly: require a positive
    ``map_id`` and a short, printable name. Used for map discovery (the Active Map
    selector), which is why only id+name — not pixels — are returned.
    """
    try:
        proto_bytes = _hex_to_proto_bytes(hex_data)
        desc = stream_pb2.MapDescription().FromString(proto_bytes)
    except Exception:
        return None
    # Strip surrounding whitespace: renaming a map to a name Eufy considers
    # "unchanged" is rejected, so users seed a name by adding a trailing space —
    # keep the visible label clean.
    name = desc.name.strip()
    if desc.map_id > 0 and name and name.isprintable() and len(name) <= 48:
        return desc.map_id, name
    return None


def _decode_path_xy(xy: int) -> tuple[int, int]:
    x = xy & 0xFFFF
    y = (xy >> 16) & 0xFFFF
    if x >= 0x8000:
        x -= 0x10000
    if y >= 0x8000:
        y -= 0x10000
    return x, y


def _exact_path_point(value: bytes) -> tuple[int, int] | None:
    """Return (xy, flags) only when the bytes are a PathPoint and nothing else.

    A loose protobuf parse treats any message whose first field is a number as a
    path point. Those numbers are not coordinates, and one of them landed in the
    living room.
    """
    if not value or len(value) > 12:
        return None
    from .proto_utils import decode_protobuf_field

    xy: int | None = None
    flags = 0
    pos = 0
    while pos < len(value):
        start = pos
        field_num, wire_type, field_value, pos = decode_protobuf_field(value, pos)
        if field_num is None or pos <= start or wire_type != 0 or not isinstance(field_value, int):
            return None
        if field_num == 1 and xy is None:
            xy = field_value
        elif field_num == 2:
            flags = field_value
        else:
            return None
    if xy is None:
        return None
    return xy, flags


def _append_path_point(
    points: list[_PathPoint],
    xy: int,
    flags: int,
) -> None:
    if not xy and not flags:
        return
    point_type = flags & 0xF
    if point_type == 15:  # HIDE
        return
    x, y = _decode_path_xy(xy)
    if x == 0 and y == 0:
        return
    break_before = bool((flags >> 4) & 1)
    # 0 sweep, 1 mop, 2 sweep+mop stay solid. Navigation, go-home, and
    # other trip types (including the 0xC points seen on transits) are dotted.
    travel = point_type not in (0, 1, 2)
    points.append((x, y, break_before, travel))


def _collect_path_points(data: bytes, out: list[_PathPoint]) -> None:
    from .proto_utils import decode_protobuf_field

    pos = 0
    while pos < len(data):
        start = pos
        field_num, wire_type, value, pos = decode_protobuf_field(data, pos)
        if field_num is None or pos <= start:
            break
        if wire_type != 2 or not isinstance(value, bytes) or len(value) < 2:
            continue
        exact = _exact_path_point(value)
        if exact is not None:
            _append_path_point(out, exact[0], exact[1])
            continue
        if len(value) >= 4:
            _collect_path_points(value, out)


def try_extract_path_points(hex_data: str) -> list[_PathPoint] | None:
    """Decode a biz/ path frame. Returns [(x_cm, y_cm, break_before, travel), ...]."""
    points: list[_PathPoint] = []
    for proto_bytes in _hex_proto_candidates(hex_data):
        exact = _exact_path_point(proto_bytes)
        if exact is not None:
            _append_path_point(points, exact[0], exact[1])
        _collect_path_points(proto_bytes, points)
    return points or None


def try_decode_as_dynamic_data(hex_data: str) -> tuple[int, int, int] | None:
    """Decode channel as DynamicData robot pose. Returns (x_cm, y_cm, theta_crad) or None."""
    for proto_bytes in _hex_proto_candidates(hex_data):
        if len(proto_bytes) > 64:
            continue
        try:
            dyn = stream_pb2.DynamicData().FromString(proto_bytes)
            pose = dyn.cur_pose
            if pose.x == 0 and pose.y == 0:
                continue
            # Reject frames that merely start with a pose-shaped field.
            clean = stream_pb2.DynamicData()
            clean.cur_pose.x = pose.x
            clean.cur_pose.y = pose.y
            clean.cur_pose.theta = pose.theta
            if clean.SerializeToString() != proto_bytes:
                continue
            return pose.x, pose.y, pose.theta
        except Exception:
            pass
    return None


def try_extract_metadata_channels(hex_data: str) -> dict[str, int] | None:
    """Extract biz/ stream channel ids from a Metadata frame."""
    for proto_bytes in _hex_proto_candidates(hex_data):
        try:
            metadata = stream_pb2.Metadata().FromString(proto_bytes)
            chan_ids = metadata.chan_ids
            channels = {
                name: getattr(chan_ids, name)
                for name in (
                    "path",
                    "dynamic_data",
                    "map_data",
                    "room_outline",
                    "room_params",
                )
                if getattr(chan_ids, name, 0)
            }
            if channels:
                return channels
        except Exception:
            pass
    return None


def parse_biz_protocol41(payload: bytes) -> tuple[int, str] | None:
    """Parse a biz/ MQTT message. Returns (channel_id, hex_data) or None."""
    try:
        msg = json.loads(payload)
        payload_data = msg.get("payload", {})
        if isinstance(payload_data, str):
            payload_data = json.loads(payload_data)
        data = payload_data.get("data", {})
        if not isinstance(data, dict):
            return None
        channel_id = data.get("channel_id")
        hex_data = data.get("data", "")
        if channel_id is None or not hex_data:
            _LOGGER.debug("biz/ missing channel_id or data — keys: %s", list(data.keys()))
            return None
        return channel_id, hex_data
    except Exception as exc:
        _LOGGER.debug("biz/ JSON parse failed: %s — first 200: %s", exc, payload[:200])
        return None


def _pose_to_pixel(
    map_data: MapData, x_cm: int, y_cm: int
) -> tuple[int, int] | None:
    """Convert robot pose (cm) to map pixel coordinates."""
    res = map_data.resolution or 5
    px = round((x_cm - map_data.origin_x) / res)
    py = round((y_cm - map_data.origin_y) / res)
    if map_data.width <= 0 or map_data.height <= 0:
        return None
    # Off-map poses used to be pinned to the border, which drew a fan of lines.
    if not (0 <= px < map_data.width and 0 <= py < map_data.height):
        return None
    return px, py


def _pose_on_map(
    map_data: MapData, x: int, y: int
) -> tuple[tuple[int, int], int, int] | None:
    """Return (pixel, x_cm, y_cm). Pose may be centimetres, or millimetres when large."""
    pixel = _pose_to_pixel(map_data, x, y)
    if pixel is not None:
        return pixel, x, y
    span = max(abs(x), abs(y))
    # A house is a few thousand cm. Larger numbers are millimetres.
    if span >= 5000:
        sx, sy = int(round(x / 10)), int(round(y / 10))
        pixel = _pose_to_pixel(map_data, sx, sy)
        if pixel is not None:
            return pixel, sx, sy
    if span >= 50000:
        sx, sy = int(round(x / 100)), int(round(y / 100))
        pixel = _pose_to_pixel(map_data, sx, sy)
        if pixel is not None:
            return pixel, sx, sy
    return None


class MapStreamHandler:
    """Accumulates map stream frames from biz/ MQTT protocol-41 messages."""

    def __init__(self, rotation: int = 0) -> None:
        self.map_data: MapData | None = None
        self.map_image: bytes | None = None
        self.map_frame: MapFrame | None = None
        self.map_channel_id: int | None = None
        self.last_seen_maps: dict[int, str] = {}
        self._robot_pixel: tuple[int, int] | None = None
        self._held_room_id: int | None = None
        self._pose_cm: tuple[int, int] | None = None
        self._pending_pose: tuple[int, int] | None = None
        self._robot_trail: list[tuple[int, int] | tuple[int, int, bool]] = []
        self._path_travel = False
        self._cleaning_path: list[_PathPoint] = []
        self._dock_pixel: tuple[int, int] | None = None
        self._tracking_active = False
        self._rotation = int(rotation) % 360
        defaults = render_settings_from_options(None)
        self._robot_style = defaults["robot_style"]
        self._robot_status = defaults["robot_status"]
        self._show_robot = defaults["show_robot"]
        self._show_trail = defaults["show_trail"]
        self._show_dock = defaults["show_dock"]
        self._max_px = defaults["max_px"]
        self._style = defaults["style"]
        self._path_channel_id: int | None = None
        self._dynamic_channel_id: int | None = None
        self._pending_room_names: dict[int, str] = {}
        self._pending_room_outline: tuple[bytes, int, int, int, int] | None = None
        self._pending_restricted_zones: RestrictedZoneLayers | None = None
        self._last_pose_render = 0.0

    def clear_trail_on_new_session(self) -> None:
        """Clear live trail when a new cleaning session starts."""
        self._robot_trail.clear()
        self._cleaning_path.clear()
        self._robot_pixel = None
        self._pose_cm = None
        self._pending_pose = None

    def set_path_travel(self, travel: bool) -> None:
        """Mark new live-trail points as a return or go-to trip."""
        self._path_travel = bool(travel)

    def _append_trail_point(self, robot_px: tuple[int, int]) -> None:
        """Append a robot pose to the live trail, filtering teleports."""
        point = (robot_px[0], robot_px[1], self._path_travel)
        if not self._robot_trail:
            self._robot_trail.append(point)
            return
        last = self._robot_trail[-1]
        dx = robot_px[0] - last[0]
        dy = robot_px[1] - last[1]
        if self.map_data is None:
            self._robot_trail.append(point)
        else:
            max_step = max(self.map_data.width, self.map_data.height) // 10
            dist_sq = dx * dx + dy * dy
            if 0 < dist_sq <= max_step * max_step:
                self._robot_trail.append(point)
        if len(self._robot_trail) > _MAX_TRAIL_POINTS:
            self._robot_trail = self._robot_trail[-_MAX_TRAIL_POINTS :]

    def _ingest_pose(self, x_cm: int, y_cm: int) -> bool:
        """Update robot pixel/trail from a world pose. Returns True when map image changed."""
        self._pending_pose = (x_cm, y_cm)
        if self.map_data is None:
            return False
        placed = _pose_on_map(self.map_data, x_cm, y_cm)
        if placed is None:
            return False
        robot_px, x_cm, y_cm = placed
        self._pose_cm = (x_cm, y_cm)
        if any(abs(x) > 4000 or abs(y) > 4000 for x, y, *_rest in self._cleaning_path):
            self._cleaning_path.clear()
        before = len(self._robot_trail)
        self._append_trail_point(robot_px)
        trail_updated = len(self._robot_trail) != before
        changed_pixel = robot_px != self._robot_pixel
        self._robot_pixel = robot_px
        now = time.monotonic()
        if (changed_pixel or trail_updated) and now - self._last_pose_render >= 1.5:
            self._last_pose_render = now
            self._render()
            return True
        return False

    def _ingest_path_points(self, path_pts: list[_PathPoint]) -> bool:
        """Merge streamed path points and re-render when possible."""
        if not path_pts:
            return False
        if self.map_data is not None:
            on_map = sum(
                1 for x_cm, y_cm, *_rest in path_pts
                if _pose_to_pixel(self.map_data, x_cm, y_cm) is not None
            )
            # One in-bounds number inside a frame of garbage is not the robot.
            if on_map == 0 or on_map * 2 < len(path_pts):
                return False
        self._cleaning_path.extend(path_pts)
        if len(self._cleaning_path) > _MAX_TRAIL_POINTS:
            self._cleaning_path = self._cleaning_path[-_MAX_TRAIL_POINTS :]
        if self.map_data is None:
            return False
        self._render()
        return True

    def _frame_matches_channel(self, channel_id: int, known_id: int | None) -> bool:
        return known_id is None or channel_id == known_id

    def set_rotation(self, rotation: int) -> None:
        """Set clockwise rotation applied when rendering (0, 90, 180, 270)."""
        self._rotation = int(rotation) % 360

    def set_render_options(self, settings: Mapping[str, Any]) -> bool:
        """Store the camera look and re-render when a map is loaded."""
        self._rotation = int(settings["rotation"]) % 360
        self._max_px = int(settings["max_px"])
        self._robot_style = settings["robot_style"]
        self._robot_status = settings["robot_status"]
        self._show_robot = settings["show_robot"]
        self._show_trail = settings["show_trail"]
        self._show_dock = settings["show_dock"]
        self._style = settings["style"]
        if self.map_data is None:
            return False
        self._render()
        return True

    def render_preview(self, settings: Mapping[str, Any]) -> bytes | None:
        """Render the current map with draft settings, without saving them."""
        if self.map_data is None:
            return None
        return render_map_png(
            self.map_data,
            robot_pixel=self._robot_pixel if settings["show_robot"] else None,
            robot_trail=(self._robot_trail or None) if settings["show_trail"] else None,
            cleaning_path=(self._cleaning_path or None)
            if settings["show_trail"]
            else None,
            dock_pixel=self._dock_pixel if settings["show_dock"] else None,
            robot_status=settings["robot_status"],
            max_px=int(settings["max_px"]),
            robot_style=settings["robot_style"],
            rotation=int(settings["rotation"]),
            style=settings["style"],
        )

    def _apply_pending_layers(self, map_data: MapData) -> None:
        if self._pending_room_names:
            map_data.room_names.update(self._pending_room_names)
        if self._pending_room_outline:
            pixels, width, height, origin_x, origin_y = self._pending_room_outline
            map_data.room_pixels = pixels
            map_data.room_outline_width = width
            map_data.room_outline_height = height
            map_data.room_outline_origin_x = origin_x
            map_data.room_outline_origin_y = origin_y
        if self._pending_restricted_zones and not map_data.has_restricted_zones():
            _apply_restricted_layers(map_data, self._pending_restricted_zones)

    def _merge_room_params(self, names: dict[int, str]) -> bool:
        self._pending_room_names.update(names)
        if self.map_data is None:
            return bool(names)
        self.map_data.room_names.update(names)
        self._render()
        return True

    def _merge_room_outline(
        self, pixels: bytes, width: int, height: int, origin_x: int, origin_y: int
    ) -> bool:
        outline = (pixels, width, height, origin_x, origin_y)
        self._pending_room_outline = outline
        if self.map_data is None:
            return True
        self.map_data.room_pixels = pixels
        self.map_data.room_outline_width = width
        self.map_data.room_outline_height = height
        self.map_data.room_outline_origin_x = origin_x
        self.map_data.room_outline_origin_y = origin_y
        self._render()
        return True

    def get_room_names(self) -> dict[int, str]:
        """Return merged room names, falling back to ids from the outline mask."""
        names: dict[int, str] = dict(self._pending_room_names)
        if self.map_data and self.map_data.room_names:
            names.update(self.map_data.room_names)

        pixels: bytes | None = None
        if self.map_data and self.map_data.room_pixels:
            pixels = self.map_data.room_pixels
        elif self._pending_room_outline:
            pixels = self._pending_room_outline[0]

        if pixels:
            for rid in _room_ids_from_pixels(pixels):
                names.setdefault(rid, f"Room {rid}")
        return names

    def _merge_restricted_zone(self, layers: RestrictedZoneLayers) -> bool:
        self._pending_restricted_zones = layers
        if self.map_data is None:
            return False
        _apply_restricted_layers(self.map_data, layers)
        self._render()
        return True

    def handle_biz_payload(self, payload: bytes) -> bool:
        """Process a biz/ MQTT payload. Returns True when map image was updated."""
        result = parse_biz_protocol41(payload)
        if result is None:
            return False

        channel_id, hex_data = result

        desc = try_extract_map_description(hex_data)
        if desc is not None:
            map_id, name = desc
            if self.last_seen_maps.get(map_id) != name:
                self.last_seen_maps[map_id] = name
            return False

        if channels := try_extract_metadata_channels(hex_data):
            if path_id := channels.get("path"):
                self._path_channel_id = path_id
            if dynamic_id := channels.get("dynamic_data"):
                self._dynamic_channel_id = dynamic_id
            if map_id := channels.get("map_data"):
                self.map_channel_id = self.map_channel_id or map_id
            return False

        # Pose frames are short, and a loose zone parse turns them into a wall at (0, 0).
        if len(hex_data) < 80:
            if pose := try_decode_as_dynamic_data(hex_data):
                return self._ingest_pose(pose[0], pose[1])

        if names := try_extract_room_params(hex_data):
            return self._merge_room_params(names)

        if outline := try_extract_room_outline(hex_data):
            pixels, width, height, origin_x, origin_y = outline
            return self._merge_room_outline(pixels, width, height, origin_x, origin_y)

        if zones := try_extract_restricted_zone(hex_data):
            return self._merge_restricted_zone(zones)

        # Small biz/ frames carry robot pose (DynamicData) and path points.
        if len(hex_data) < 200:
            if pose := try_decode_as_dynamic_data(hex_data):
                return self._ingest_pose(pose[0], pose[1])
            if path_pts := try_extract_path_points(hex_data):
                return self._ingest_path_points(path_pts)
            return False

        is_map_candidate = (
            self.map_channel_id is None
            or channel_id == self.map_channel_id
            or len(hex_data) > 15000
        )
        map_data = try_extract_map_data(hex_data) if is_map_candidate else None

        if map_data is None and self._frame_matches_channel(
            channel_id, self._path_channel_id
        ):
            if path_pts := try_extract_path_points(hex_data):
                return self._ingest_path_points(path_pts)

        if map_data is None and self._frame_matches_channel(
            channel_id, self._dynamic_channel_id
        ):
            if pose := try_decode_as_dynamic_data(hex_data):
                return self._ingest_pose(pose[0], pose[1])

        if len(hex_data) < 800:
            if path_pts := try_extract_path_points(hex_data):
                return self._ingest_path_points(path_pts)
            if pose := try_decode_as_dynamic_data(hex_data):
                return self._ingest_pose(pose[0], pose[1])
            return False

        if map_data is None:
            if pose := try_decode_as_dynamic_data(hex_data):
                return self._ingest_pose(pose[0], pose[1])
            return False

        if self.map_channel_id != channel_id:
            self.map_channel_id = channel_id
            _LOGGER.debug("Discovered map channel %d", channel_id)

        if self.map_data is not None and map_data.room_pixels is None:
            map_data.room_pixels = self.map_data.room_pixels
            map_data.room_outline_width = self.map_data.room_outline_width
            map_data.room_outline_height = self.map_data.room_outline_height
            map_data.room_outline_origin_x = self.map_data.room_outline_origin_x
            map_data.room_outline_origin_y = self.map_data.room_outline_origin_y
            if self.map_data.room_names:
                map_data.room_names = dict(self.map_data.room_names)
            map_data.virtual_walls = self.map_data.virtual_walls
            map_data.forbidden_zones = self.map_data.forbidden_zones
            map_data.ban_mop_zones = self.map_data.ban_mop_zones
            if map_data.dock_cm is None:
                map_data.dock_cm = self.map_data.dock_cm

        self._apply_pending_layers(map_data)
        self._place_dock(map_data)
        self.map_data = map_data
        posed = False
        if self._pending_pose is not None:
            self._last_pose_render = 0.0
            posed = self._ingest_pose(self._pending_pose[0], self._pending_pose[1])
        if not posed:
            self._render()
        return True

    def set_tracking_active(self, active: bool) -> None:
        """Enable live trail accumulation while the robot is moving on the map."""
        if active and not self._tracking_active:
            # Keep historical path/pose from the current map download.
            pass
        elif not active and self._tracking_active:
            # Robot stopped moving; keep the last rendered trail and pose.
            pass
        self._tracking_active = active

    def set_tracking_cleaning(self, active: bool) -> None:
        """Backward-compatible alias for older callers."""
        self.set_tracking_active(active)

    def has_restricted_zones(self) -> bool:
        """Return True when zone geometry is available on the map or pending."""
        if self.map_data and self.map_data.has_restricted_zones():
            return True
        return (
            self._pending_restricted_zones is not None
            and not self._pending_restricted_zones.is_empty()
        )

    def has_cleaning_path(self) -> bool:
        """Return True when streamed or embedded cleaning path data exists."""
        if self._cleaning_path or self._robot_trail:
            return True
        if self.map_data and self.map_data.raw_pixels:
            return bool(_visited_path_pixels(self.map_data))
        return False

    @property
    def robot_pixel(self) -> tuple[int, int] | None:
        """Last known robot position in map pixel coordinates."""
        return self._robot_pixel

    def robot_room(self) -> tuple[int, str] | None:
        """Room under the robot. A wall or doorway keeps the last real room."""
        pixel = self._robot_pixel
        if pixel is not None and self.map_data is not None:
            rid, _sub = _room_mask_at(self.map_data, pixel[0], pixel[1])
            if _is_valid_room_id(rid):
                self._held_room_id = rid
        if self._held_room_id is None:
            return None
        return (
            self._held_room_id,
            self.get_room_names().get(self._held_room_id, f"Room {self._held_room_id}"),
        )

    def status_text(self) -> str:
        """One line for the live map page."""
        pixel = self._robot_pixel
        pose = self._pose_cm
        found = self.robot_room()
        room = found[1] if found else ""
        pixel_text = f"{pixel[0]},{pixel[1]}" if pixel else "none"
        pose_text = f"{pose[0]},{pose[1]} cm" if pose else "no pose yet"
        dock = self._dock_pixel
        dock_text = f"{dock[0]},{dock[1]}" if dock else "none"
        return (
            f"robot pixel {pixel_text} · pose {pose_text} · "
            f"{room or 'room unknown'} · dock {dock_text} · "
            f"path points {len(self._cleaning_path)}"
        )

    def _place_dock(self, map_data: MapData) -> None:
        """Put the charger icon on the map pose, when that pose is on the floor."""
        if map_data.dock_cm is None:
            return
        placed = _pose_on_map(map_data, map_data.dock_cm[0], map_data.dock_cm[1])
        if placed is not None:
            self._dock_pixel = placed[0]

    def export_map_snapshot(self) -> dict[str, Any] | None:
        """JSON-safe copy of the floor, so a restart can show it before MQTT."""
        map_data = self.map_data
        if map_data is None:
            return None
        return {
            "width": map_data.width,
            "height": map_data.height,
            "origin_x": map_data.origin_x,
            "origin_y": map_data.origin_y,
            "resolution": map_data.resolution,
            "raw_pixels": base64.b64encode(map_data.raw_pixels).decode(),
            "room_pixels": (
                base64.b64encode(map_data.room_pixels).decode()
                if map_data.room_pixels
                else None
            ),
            "room_outline_width": map_data.room_outline_width,
            "room_outline_height": map_data.room_outline_height,
            "room_outline_origin_x": map_data.room_outline_origin_x,
            "room_outline_origin_y": map_data.room_outline_origin_y,
            "room_names": {str(k): v for k, v in map_data.room_names.items()},
            "virtual_walls": map_data.virtual_walls,
            "forbidden_zones": map_data.forbidden_zones,
            "ban_mop_zones": map_data.ban_mop_zones,
            "dock_cm": list(map_data.dock_cm) if map_data.dock_cm else None,
            "robot_pixel": list(self._robot_pixel) if self._robot_pixel else None,
            "map_id": map_data.map_id,
            "releases": map_data.releases,
        }

    def restore_map_snapshot(self, payload: dict[str, Any]) -> bool:
        """Load a saved floor and render it. Returns False when the payload is unusable."""
        try:
            raw = base64.b64decode(payload["raw_pixels"])
            room_raw = payload.get("room_pixels")
            dock = payload.get("dock_cm")
            map_data = MapData(
                raw_pixels=raw,
                width=int(payload["width"]),
                height=int(payload["height"]),
                origin_x=int(payload.get("origin_x", 0)),
                origin_y=int(payload.get("origin_y", 0)),
                resolution=int(payload.get("resolution") or 5),
                room_pixels=base64.b64decode(room_raw) if room_raw else None,
                room_outline_width=int(payload.get("room_outline_width") or 0),
                room_outline_height=int(payload.get("room_outline_height") or 0),
                room_outline_origin_x=int(payload.get("room_outline_origin_x") or 0),
                room_outline_origin_y=int(payload.get("room_outline_origin_y") or 0),
                room_names={
                    int(k): str(v) for k, v in (payload.get("room_names") or {}).items()
                },
                virtual_walls=payload.get("virtual_walls") or [],
                forbidden_zones=payload.get("forbidden_zones") or [],
                ban_mop_zones=payload.get("ban_mop_zones") or [],
                dock_cm=(int(dock[0]), int(dock[1])) if dock else None,
                map_id=int(payload.get("map_id") or 0),
                releases=int(payload.get("releases") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return False
        robot = payload.get("robot_pixel")
        self._robot_pixel = (int(robot[0]), int(robot[1])) if robot else None
        self.map_data = map_data
        self._place_dock(map_data)
        self._render()
        return self.map_image is not None

    def restricted_zone_counts(self) -> dict[str, int]:
        """Return counts of virtual walls and zone polygons."""
        source = self.map_data
        if source is None or not source.has_restricted_zones():
            source = self._pending_restricted_zones
        if source is None:
            return {
                "virtual_walls": 0,
                "forbidden_zones": 0,
                "ban_mop_zones": 0,
            }
        if isinstance(source, RestrictedZoneLayers):
            return {
                "virtual_walls": len(source.virtual_walls),
                "forbidden_zones": len(source.forbidden_zones),
                "ban_mop_zones": len(source.ban_mop_zones),
            }
        return {
            "virtual_walls": len(source.virtual_walls),
            "forbidden_zones": len(source.forbidden_zones),
            "ban_mop_zones": len(source.ban_mop_zones),
        }

    def _render(self) -> None:
        if self.map_data is None:
            return
        try:
            frame_out: list[MapFrame] = []
            self.map_image = render_map_png(
                self.map_data,
                robot_pixel=self._robot_pixel if self._show_robot else None,
                robot_trail=(self._robot_trail or None) if self._show_trail else None,
                cleaning_path=(self._cleaning_path or None)
                if self._show_trail
                else None,
                dock_pixel=self._dock_pixel if self._show_dock else None,
                robot_status=self._robot_status,
                max_px=self._max_px,
                robot_style=self._robot_style,
                rotation=self._rotation,
                style=self._style,
                frame_out=frame_out,
            )
            self.map_frame = frame_out[0] if frame_out else None
        except Exception as exc:
            _LOGGER.debug("Map render failed: %s", exc)

    def picture_hit(self, nx: float, ny: float) -> dict[str, Any]:
        """Room and zone under a point on the current picture."""
        empty = {"room_id": 0, "room_name": "", "zone_kind": "", "zone_index": -1}
        if self.map_data is None or self.map_frame is None:
            return empty
        return picture_hit(self.map_data, self.map_frame, nx, ny)

    def picture_zone_boxes(self) -> dict[str, list[dict[str, Any]]]:
        """No-go and no-mop boxes on the current picture."""
        if self.map_data is None or self.map_frame is None:
            return {"nogo": [], "nomop": []}
        return picture_zone_boxes(self.map_data, self.map_frame)

    def picture_rect_quad(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> list[tuple[int, int]] | None:
        """Picture rectangle to centimetre corners, or None when no map is drawn."""
        if self.map_frame is None:
            return None
        return picture_rect_to_quad(self.map_frame, x1, y1, x2, y2)
