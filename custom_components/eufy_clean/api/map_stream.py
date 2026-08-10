"""Parse biz/ MQTT protocol-41 map stream messages and render PNG."""
from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field
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

# Background outside the room partition mask (matches app-style clipping).
_BG_OUTSIDE: tuple[int, int, int] = (45, 45, 45)

# Darker trace for cleaned / visited pixels inside a room.
_PATH_INSIDE_ROOM: tuple[int, int, int] = (210, 110, 35)

# Orange overlay for streamed trajectory and visited-pixel highlights.
_PATH_OVERLAY: tuple[int, int, int] = (255, 140, 0)

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


def _visited_path_pixels(
    map_data: MapData,
) -> list[tuple[int, int]]:
    """Return map pixels that look like a cleaning trace (pv=0 on explored floor)."""
    raw = map_data.raw_pixels
    width, height = map_data.width, map_data.height
    room_px = map_data.room_pixels
    res = map_data.resolution or 5
    points: list[tuple[int, int]] = []

    def _room_id(px_x: int, py: int) -> int:
        if (
            room_px is None
            or not map_data.room_outline_width
            or not map_data.room_outline_height
        ):
            return 0
        ro_w = map_data.room_outline_width
        ro_h = map_data.room_outline_height
        ro_dx = round((map_data.origin_x - map_data.room_outline_origin_x) / res)
        ro_dy = round((map_data.origin_y - map_data.room_outline_origin_y) / res)
        rx, ry = px_x - ro_dx, py - ro_dy
        if 0 <= rx < ro_w and 0 <= ry < ro_h:
            return room_px[ry * ro_w + rx] >> 2
        return 0

    for py in range(height):
        for px_x in range(width):
            pv = _map_pixel_value(raw, width, px_x, py)
            if pv not in (0, 3):
                continue
            rid = _room_id(px_x, py)
            touches_floor = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = px_x + dx, py + dy
                if 0 <= nx < width and 0 <= ny < height:
                    neighbor = _map_pixel_value(raw, width, nx, ny)
                    if neighbor in (2, 3):
                        touches_floor = True
                        break
            if not touches_floor:
                continue
            if room_px is not None:
                if _is_valid_room_id(rid) or rid == _ROOM_MASK_GAP:
                    points.append((px_x, py))
            elif pv == 0:
                points.append((px_x, py))
    return points


def _is_valid_room_id(rid: int) -> bool:
    return 1 <= rid <= 31


def _tint(rgb: tuple[int, int, int], delta: int) -> tuple[int, int, int]:
    return tuple(max(0, min(255, c + delta)) for c in rgb)


def _color_with_room_mask(
    pv: int,
    rid: int,
    sub_type: int,
    palette_len: int,
) -> tuple[int, int, int]:
    """Render using the room partition mask to clip lidar exploration noise.

    Inside a labelled room: solid room colour except true walls and the
    darker cleaning trace (unknown / carpet pixels).
    Outside rooms: dark background with only obstacle pixels kept (wall clip).
    """
    if rid == _ROOM_MASK_OBSTACLE or pv == 1 or sub_type == 1:
        return _PIXEL_COLORS[1]
    if _is_valid_room_id(rid):
        base = _ROOM_PALETTE[1 + (rid - 1) % (palette_len - 1)]
        if pv == 0:
            return _PATH_INSIDE_ROOM
        if pv == 3:
            return _tint(base, -35)
        return base
    if rid == _ROOM_MASK_GAP:
        if pv == 0:
            return _PATH_INSIDE_ROOM
        return _PIXEL_COLORS[2]
    # Outside partitioned rooms (rid 0, or unused ids): clip exploration noise.
    return _BG_OUTSIDE


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

    def has_restricted_zones(self) -> bool:
        """Return True when any no-go, no-mop, or virtual-wall data is present."""
        return bool(self.virtual_walls or self.forbidden_zones or self.ban_mop_zones)

    def room_id_at_normalized(self, nx: float, ny: float) -> int:
        """Return the room id under a normalized point on the *rendered* map image.

        ``(nx, ny)`` are fractions (0-1) of the rendered PNG, top-left origin (the
        HA camera-image convention — the same space the zone card draws in). This
        is the exact inverse of ``render_map_png``'s room-mask lookup: map the point
        back to a source grid pixel (undoing the baked-in Y-flip, exactly as
        ``normalized_rects_to_quads_cm`` does), then index ``room_pixels`` with the
        same outline origin offset the renderer uses. Returns 0 when there is no
        room mask, the point is outside any room, or no map has been decoded yet.
        """
        if not self.room_pixels or not self.room_outline_width or not self.room_outline_height:
            return 0
        nx = min(max(float(nx), 0.0), 1.0)
        ny = min(max(float(ny), 0.0), 1.0)
        res = self.resolution or 5
        w, h = self.width, self.height
        # normalized (rendered, top-left) -> source grid pixel; the render Y-flips,
        # so the displayed top edge is the source bottom row.
        px = min(max(round(nx * w), 0), max(w - 1, 0))
        py = min(max(round((h - 1) - ny * h), 0), max(h - 1, 0))
        # the room mask has its own origin; this offset matches render_map_png's _ro_dx/_ro_dy.
        ro_dx = round((self.origin_x - self.room_outline_origin_x) / res)
        ro_dy = round((self.origin_y - self.room_outline_origin_y) / res)
        rx, ry = px - ro_dx, py - ro_dy
        if 0 <= rx < self.room_outline_width and 0 <= ry < self.room_outline_height:
            idx = ry * self.room_outline_width + rx
            if 0 <= idx < len(self.room_pixels):
                return self.room_pixels[idx] >> 2  # low 2 bits are sub-type, not id
        return 0


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


def _parse_restricted_zone(rz: Any) -> RestrictedZoneLayers:
    layers = RestrictedZoneLayers()
    for wall in rz.virtual_walls:
        layers.virtual_walls.append(
            ((wall.p0.x, wall.p0.y), (wall.p1.x, wall.p1.y))
        )
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
    cleaning_path: list[tuple[int, int, bool]] | None = None,
    dock_pixel: tuple[int, int] | None = None,
    robot_status: str | None = None,
    max_px: int = _MAX_PNG_PX,
    robot_style: str = "googly",
    rotation: int = 0,
) -> bytes:
    """Render a PNG from MapData using Pillow.

    Pipeline:
    1. Build flat color list from map pixels (room palette + lidar fallback).
    2. putdata() into PIL Image, Y-flip, LANCZOS scale.
    3. Draw restricted zones, room labels, dock icon, trail, robot marker.
    4. Encode to PNG bytes via img.save().
    """
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

    if room_px is not None and map_data.room_outline_width and map_data.room_outline_height:
        _ro_w = map_data.room_outline_width
        _ro_h = map_data.room_outline_height
        _ro_dx = round((map_data.origin_x - map_data.room_outline_origin_x) / res)
        _ro_dy = round((map_data.origin_y - map_data.room_outline_origin_y) / res)
        palette_len = len(_ROOM_PALETTE)
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
                color = _color_with_room_mask(pv, rid, sub_type, palette_len)
                colors.append(color)
                if _has_room_names and rid > 0 and rid in map_data.room_names:
                    if rid not in src_centroids:
                        src_centroids[rid] = [0, 0, 0]
                    src_centroids[rid][0] += px_x
                    src_centroids[rid][1] += py
                    src_centroids[rid][2] += 1
    else:
        for i in range(width * height):
            byte_pos = i >> 2
            bit_pos = (i & 3) * 2
            pv = (raw[byte_pos] >> bit_pos) & 3 if byte_pos < len(raw) else 0
            colors.append(_PIXEL_COLORS.get(pv, (30, 30, 30)))

    # ------------------------------------------------------------------
    # Step 2 — create PIL image, Y-flip, scale
    # ------------------------------------------------------------------
    img: Image.Image = Image.new("RGB", (width, height))
    img.putdata(colors)
    img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    scale = min(max_px / max(width, height), 1.0)
    out_w = max(1, round(width * scale))
    out_h = max(1, round(height * scale))
    if scale < 1.0:
        img = img.resize((out_w, out_h), Image.Resampling.LANCZOS)

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
    # ------------------------------------------------------------------
    _BAN_MOP_COLOR = (255, 165, 0)
    _ZONE_COLOR = (220, 50, 50)

    for zone in map_data.ban_mop_zones:
        pts = [_world_to_out(p[0], p[1]) for p in zone]
        if len(pts) >= 2:
            draw.polygon(pts, outline=_BAN_MOP_COLOR)

    for zone in map_data.forbidden_zones:
        pts = [_world_to_out(p[0], p[1]) for p in zone]
        if len(pts) >= 2:
            draw.polygon(pts, outline=_ZONE_COLOR)

    for wall in map_data.virtual_walls:
        draw.line(
            [_world_to_out(wall[0][0], wall[0][1]), _world_to_out(wall[1][0], wall[1][1])],
            fill=_ZONE_COLOR,
        )

    # ------------------------------------------------------------------
    # Step 4 — dock icon (pixel-art house)
    # (labels drawn after trail in step 6 so they render on top)
    # ------------------------------------------------------------------
    if dock_pixel is not None:
        dx, dy = _to_out(dock_pixel[0], dock_pixel[1])
        _house_border = {
            (nx, ny)
            for ox, oy in _HOUSE_FILL
            for nx, ny in ((ox + ddx, oy + ddy) for ddx in (-1, 0, 1) for ddy in (-1, 0, 1))
            if (nx, ny) not in _HOUSE_FILL
        }
        for ox, oy in _house_border:
            bpx, bpy = dx + ox, dy + oy
            if 0 <= bpx < out_w and 0 <= bpy < out_h:
                draw.point((bpx, bpy), fill=(100, 75, 0))
        for ox, oy in _HOUSE_FILL:
            bpx, bpy = dx + ox, dy + oy
            if 0 <= bpx < out_w and 0 <= bpy < out_h:
                draw.point((bpx, bpy), fill=(255, 215, 0))
        for ox, oy in _HOUSE_DOOR:
            bpx, bpy = dx + ox, dy + oy
            if 0 <= bpx < out_w and 0 <= bpy < out_h:
                draw.point((bpx, bpy), fill=(100, 75, 0))

    # ------------------------------------------------------------------
    # Step 5 — cleaning trail / streamed path
    # ------------------------------------------------------------------
    _MAX_TRAIL_JUMP_SQ = 400 * 400

    def _draw_path_segments(
        raw_pts: list[tuple[int, int]],
        breaks: list[bool] | None = None,
    ) -> None:
        if len(raw_pts) < 2:
            return
        out_pts = [_to_out(tx, ty) for tx, ty in raw_pts]
        for i in range(len(raw_pts) - 1):
            if breaks and i + 1 < len(breaks) and breaks[i + 1]:
                continue
            ddx = raw_pts[i + 1][0] - raw_pts[i][0]
            ddy = raw_pts[i + 1][1] - raw_pts[i][1]
            if ddx * ddx + ddy * ddy <= _MAX_TRAIL_JUMP_SQ:
                draw.line([out_pts[i], out_pts[i + 1]], fill=_PATH_OVERLAY)
        ox, oy = out_pts[-1]
        if 0 <= ox < out_w and 0 <= oy < out_h:
            draw.point((ox, oy), fill=_PATH_OVERLAY)

    if cleaning_path:
        path_pixels: list[tuple[int, int]] = []
        path_breaks: list[bool] = []
        for x_cm, y_cm, break_before in cleaning_path:
            px = _pose_to_pixel(map_data, x_cm, y_cm)
            if px is None:
                continue
            path_pixels.append(px)
            path_breaks.append(break_before)
        _draw_path_segments(path_pixels, path_breaks)

    if robot_trail:
        _draw_path_segments(list(robot_trail))

    if map_data.raw_pixels and not robot_trail:
        for px_x, py in _visited_path_pixels(map_data):
            ox, oy = _to_out(px_x, py)
            if 0 <= ox < out_w and 0 <= oy < out_h:
                draw.point((ox, oy), fill=_PATH_OVERLAY)

    # ------------------------------------------------------------------
    # Step 6 — room name labels (after trail so labels render on top)
    # ------------------------------------------------------------------
    if src_centroids:
        try:
            font: ImageFont.ImageFont | ImageFont.FreeTypeFont = ImageFont.load_default(size=9)
        except TypeError:
            font = ImageFont.load_default()
        _LABEL_COLOR = (30, 30, 30)
        _LABEL_BG = (255, 255, 255)
        for rid, vals in src_centroids.items():
            if vals[2] == 0:
                continue
            label = map_data.room_names[rid].upper()
            if not label:
                continue
            ox, oy = _to_out(vals[0] // vals[2], vals[1] // vals[2])
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.rectangle(
                [(ox - tw // 2 - 2, oy - th // 2 - 1), (ox + tw // 2 + 2, oy + th // 2 + 1)],
                fill=_LABEL_BG,
            )
            draw.text((ox - tw // 2 - bbox[0], oy - th // 2 - bbox[1]), label, fill=_LABEL_COLOR, font=font)

    # ------------------------------------------------------------------
    # Step 7 — robot marker + status badge
    # ------------------------------------------------------------------
    if robot_pixel is not None:
        orx, ory = _to_out(robot_pixel[0], robot_pixel[1])
        if robot_style == "dot":
            _circle(orx, ory, 5.0, (20, 20, 20))
            _circle(orx, ory, 4.0, (55, 55, 55))
        else:  # "googly" (default)
            _circle(orx, ory, 5.0, (160, 70, 0))
            _circle(orx, ory, 4.0, (255, 140, 0))
            for ex, ey in ((-1, -1), (2, -1)):
                _circle(orx + ex, ory + ey, 1.5, (255, 255, 255))
                _circle(orx + ex, ory + ey, 0.6, (20, 20, 20))

        if robot_status and robot_status in _STATUS_BADGE:
            badge_color, icon_offsets = _STATUS_BADGE[robot_status]
            bx, by = orx + 6, ory - 6
            _circle(bx, by, 6.0, (30, 30, 30))
            _circle(bx, by, 5.0, badge_color)
            for ox2, oy2 in icon_offsets:
                bpx, bpy = bx + ox2, by + oy2
                if 0 <= bpx < out_w and 0 <= bpy < out_h:
                    draw.point((bpx, bpy), fill=(20, 20, 20))

    # ------------------------------------------------------------------
    # Step 8 — optional rotation + encode PNG
    # ------------------------------------------------------------------
    if rotation:
        img = _rotate_image(img, rotation)

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


def _append_path_point(
    points: list[tuple[int, int, bool]],
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
    points.append((x, y, break_before))


def _collect_path_points(data: bytes, out: list[tuple[int, int, bool]]) -> None:
    from .proto_utils import decode_protobuf_field

    pos = 0
    while pos < len(data):
        field_num, wire_type, value, pos = decode_protobuf_field(data, pos)
        if field_num is None:
            break
        if wire_type != 2 or not isinstance(value, bytes):
            continue
        try:
            pp = stream_pb2.PathPoint().FromString(value)
            if pp.xy or pp.flags:
                _append_path_point(out, pp.xy, pp.flags)
                continue
        except Exception:
            pass
        if len(value) >= 4:
            _collect_path_points(value, out)


def try_extract_path_points(hex_data: str) -> list[tuple[int, int, bool]] | None:
    """Decode a biz/ path frame. Returns [(x_cm, y_cm, break_before), ...]."""
    points: list[tuple[int, int, bool]] = []
    for proto_bytes in _hex_proto_candidates(hex_data):
        try:
            pp = stream_pb2.PathPoint().FromString(proto_bytes)
            _append_path_point(points, pp.xy, pp.flags)
        except Exception:
            pass
        _collect_path_points(proto_bytes, points)
    return points or None


def try_decode_as_dynamic_data(hex_data: str) -> tuple[int, int, int] | None:
    """Decode channel as DynamicData robot pose. Returns (x_cm, y_cm, theta_crad) or None."""
    try:
        proto_bytes = _hex_to_proto_bytes(hex_data)
        dyn = stream_pb2.DynamicData().FromString(proto_bytes)
        pose = dyn.cur_pose
        if pose.x != 0 or pose.y != 0:
            return pose.x, pose.y, pose.theta
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
    if 0 <= px < map_data.width and 0 <= py < map_data.height:
        return px, py
    return None


class MapStreamHandler:
    """Accumulates map stream frames from biz/ MQTT protocol-41 messages."""

    def __init__(self, rotation: int = 0) -> None:
        self.map_data: MapData | None = None
        self.map_image: bytes | None = None
        self.map_channel_id: int | None = None
        self.last_seen_maps: dict[int, str] = {}
        self._robot_pixel: tuple[int, int] | None = None
        self._robot_trail: list[tuple[int, int]] = []
        self._cleaning_path: list[tuple[int, int, bool]] = []
        self._dock_pixel: tuple[int, int] | None = None
        self._tracking_cleaning = False
        self._rotation = rotation
        self._pending_room_names: dict[int, str] = {}
        self._pending_room_outline: tuple[bytes, int, int, int, int] | None = None
        self._pending_restricted_zones: RestrictedZoneLayers | None = None

    def set_rotation(self, rotation: int) -> None:
        """Set clockwise rotation applied when rendering (0, 90, 180, 270)."""
        self._rotation = int(rotation) % 360

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

        if names := try_extract_room_params(hex_data):
            return self._merge_room_params(names)

        if outline := try_extract_room_outline(hex_data):
            pixels, width, height, origin_x, origin_y = outline
            return self._merge_room_outline(pixels, width, height, origin_x, origin_y)

        if zones := try_extract_restricted_zone(hex_data):
            return self._merge_restricted_zone(zones)

        if len(hex_data) < 800:
            if path_pts := try_extract_path_points(hex_data):
                self._cleaning_path.extend(path_pts)
                if self.map_data is not None:
                    self._render()
                    return True
            pose = try_decode_as_dynamic_data(hex_data)
            if pose is not None and self.map_data is not None:
                robot_px = _pose_to_pixel(self.map_data, pose[0], pose[1])
                if robot_px is not None:
                    if self._tracking_cleaning:
                        if not self._robot_trail:
                            self._robot_trail.append(robot_px)
                        else:
                            dx = robot_px[0] - self._robot_trail[-1][0]
                            dy = robot_px[1] - self._robot_trail[-1][1]
                            max_step = max(self.map_data.width, self.map_data.height) // 10
                            dist_sq = dx * dx + dy * dy
                            if 3 <= dist_sq <= max_step * max_step:
                                self._robot_trail.append(robot_px)
                    self._robot_pixel = robot_px
                    self._render()
                    return True
            return False

        is_map_candidate = (
            self.map_channel_id is None
            or channel_id == self.map_channel_id
            or len(hex_data) > 15000
        )
        if not is_map_candidate:
            return False

        map_data = try_extract_map_data(hex_data)
        if map_data is None:
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

        self._apply_pending_layers(map_data)
        self.map_data = map_data
        self._render()
        return True

    def set_tracking_cleaning(self, active: bool) -> None:
        """Enable trail accumulation while the robot is cleaning."""
        if active and not self._tracking_cleaning:
            self._robot_trail.clear()
            self._cleaning_path.clear()
            self._robot_pixel = None
        elif not active and self._tracking_cleaning and self._robot_pixel is not None:
            self._dock_pixel = self._robot_pixel
        self._tracking_cleaning = active

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
            self.map_image = render_map_png(
                self.map_data,
                robot_pixel=self._robot_pixel,
                robot_trail=self._robot_trail or None,
                cleaning_path=self._cleaning_path or None,
                dock_pixel=self._dock_pixel,
                rotation=self._rotation,
            )
        except Exception as exc:
            _LOGGER.debug("Map render failed: %s", exc)
