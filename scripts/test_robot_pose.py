#!/usr/bin/env python3
"""Robot pose comes from DynamicData, not a stray path number."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import download_map_p2p as dl

dl.bootstrap_eufy_modules()

from eufy_clean.api.map_stream import (  # noqa: E402
    MapData,
    MapStreamHandler,
    _dock_cm_from_info,
    _rotated_point,
    render_settings_from_options,
    _append_path_point,
    _crop_to_content,
    _floor_leaked_past_walls,
    _map_pixel_value,
    _exact_path_point,
    _pose_on_map,
    stream_pb2,
    try_extract_restricted_zone,
)


def main() -> None:
    point = stream_pb2.PathPoint(xy=71, flags=1)
    assert _exact_path_point(point.SerializeToString()) == (71, 1)
    bigger = bytes([0x08, 0x96, 0x01, 0x18, 0x05, 0x22, 0x04, 1, 2, 3, 4])
    assert _exact_path_point(bigger) is None

    floor = MapData(
        raw_pixels=b"",
        width=159,
        height=353,
        origin_x=-270,
        origin_y=-1035,
        resolution=5,
    )
    # Bedroom centroid is about (47, 53). These millimetres are that spot.
    placed = _pose_on_map(floor, -350, -7700)
    assert placed is not None
    pixel, x_cm, y_cm = placed
    assert abs(pixel[0] - 47) <= 1
    assert abs(pixel[1] - 53) <= 1
    assert (x_cm, y_cm) == (-35, -770)
    assert _pose_on_map(floor, -35, -770)[0] == pixel

    handler = MapStreamHandler()
    handler.map_data = floor
    junk = [(539, 0, True), (71, 0, False)] + [(29824, 32387, False)] * 26
    assert handler._ingest_path_points(junk) is False
    assert handler.robot_pixel is None

    handler._ingest_pose(-350, -7700)
    assert handler.robot_pixel == pixel

    # A live pose frame must not be swallowed as a wall at (0, 0).
    pose_hex = "0A0A08088A0110E1011811"
    assert try_extract_restricted_zone(pose_hex) is None
    house = MapData(
        raw_pixels=b"\x00" * ((159 * 353 + 3) // 4),
        width=159,
        height=353,
        origin_x=-270,
        origin_y=-1035,
        resolution=5,
    )
    live = MapStreamHandler(rotation=0)
    live.map_data = house
    import json
    body = json.dumps(
        {"payload": {"data": {"channel_id": 7, "data": pose_hex}}}
    ).encode()
    assert live.handle_biz_payload(body) is True
    assert live.robot_pixel == (68, 184)
    info = stream_pb2.MapInfo()
    info.docks.add(x=257, y=-859)
    assert _dock_cm_from_info(info) == (257, -859)
    house.dock_cm = (257, -859)
    live._place_dock(house)
    assert live._dock_pixel == (105, 35)
    assert _rotated_point(2, 5, 10, 20, 90) == (14, 2)
    assert _rotated_point(2, 5, 10, 20, 180) == (7, 14)
    assert _rotated_point(2, 5, 10, 20, 270) == (5, 7)
    remembered = MapStreamHandler()
    remembered.map_data = house
    remembered._robot_pixel = (68, 184)
    snapshot = remembered.export_map_snapshot()
    assert snapshot is not None
    restored = MapStreamHandler()
    assert restored.restore_map_snapshot(snapshot) is True
    assert restored.map_data is not None
    assert restored.map_data.width == house.width
    assert restored.robot_pixel == (68, 184)
    style = render_settings_from_options({})["style"]
    assert style["icon_scale"] == 0.5
    assert style["path_spacing"] == 1.0
    assert style["path_alpha"] == 0.5
    clean_pts: list = []
    travel_pts: list = []
    # flags 0 is sweep. 0x4C is a transit type seen on go-to and return trips.
    _append_path_point(clean_pts, 100, 0)
    _append_path_point(travel_pts, 100, 0x4C)
    assert clean_pts[0][3] is False
    assert travel_pts[0][3] is True
    # A floor ray outside a closed wall is hidden. The floor inside the wall stays.
    side = 10
    raw = bytearray((side * side + 3) // 4)

    def put(x: int, y: int, pv: int) -> None:
        i = y * side + x
        raw[i >> 2] |= (pv & 3) << ((i & 3) * 2)

    for x in range(2, 8):
        put(x, 2, 1)
        put(x, 7, 1)
    for y in range(2, 8):
        put(2, y, 1)
        put(7, y, 1)
    for y in range(3, 7):
        for x in range(3, 7):
            put(x, y, 2)
    put(8, 4, 2)
    put(9, 4, 2)
    leaked = _floor_leaked_past_walls(bytes(raw), side, side, None, 0, 0, 0, 0)
    assert leaked[4 * side + 8] and leaked[4 * side + 9]
    assert not leaked[4 * side + 4]
    from PIL import Image as _Image

    canvas = _Image.new("RGB", (80, 60), (30, 30, 30))
    canvas.paste((100, 150, 200), (30, 20, 50, 40))
    cropped = _crop_to_content(canvas, (30, 30, 30))
    assert cropped.size == (20 + 32, 20 + 32)
    tight = render_settings_from_options({"icon_scale": 0.01, "path_spacing": 9})["style"]
    assert tight["icon_scale"] == 0.01
    assert tight["path_spacing"] == 8.0
    import base64

    from eufy_clean.api.map_commands import build_set_zones_command
    from eufy_clean.api.map_stream import (
        picture_rect_to_quad,
        picture_room_id,
        render_map_png,
        world_to_picture,
        picture_to_world,
    )
    from eufy_clean.api.proto_utils import decode_varint, encode_square_clean_command
    from eufy_clean.proto.cloud import control_pb2, map_edit_pb2

    encoded = base64.b64decode(
        encode_square_clean_command(100, -200, 80, map_id=7, releases=3)
    )
    assert encoded[0] == len(encoded) - 1
    req = control_pb2.ModeCtrlRequest()
    req.ParseFromString(encoded[1:])
    assert req.method == control_pb2.ModeCtrlRequest.START_SELECT_ZONES_CLEAN
    zone = req.select_zones_clean.zones[0]
    assert (zone.quadrangle.p0.x, zone.quadrangle.p0.y) == (60, -240)
    assert (zone.quadrangle.p2.x, zone.quadrangle.p2.y) == (140, -160)
    assert zone.clean_times == 1
    assert req.select_zones_clean.map_id == 7
    assert req.select_zones_clean.releases == 3
    house.map_id = 7
    house.releases = 3
    remembered.map_data = house
    again = remembered.export_map_snapshot()
    assert again is not None and again["map_id"] == 7 and again["releases"] == 3

    src_w, src_h = 40, 80
    room = bytearray(src_w * src_h)
    for y in range(36, 45):
        for x in range(18, 23):
            room[y * src_w + x] = 4
    tiny = MapData(
        raw_pixels=b"",
        width=src_w,
        height=src_h,
        origin_x=-100,
        origin_y=-50,
        resolution=5,
        room_pixels=bytes(room),
        room_outline_width=src_w,
        room_outline_height=src_h,
        room_outline_origin_x=-100,
        room_outline_origin_y=-50,
        room_names={1: "A"},
    )
    frames: list = []
    png = render_map_png(
        tiny,
        rotation=90,
        max_px=512,
        style={"render_scale": 1, "show_labels": False, "show_zones": False},
        frame_out=frames,
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    frame = frames[0]
    assert frame.rotation == 90 and frame.crop_left > 0
    wx, wy = frame.origin_x + 20 * frame.resolution, frame.origin_y + 40 * frame.resolution
    back = picture_to_world(frame, *world_to_picture(frame, wx, wy))
    assert abs(back[0] - wx) <= frame.resolution
    assert abs(back[1] - wy) <= frame.resolution
    assert picture_room_id(tiny, frame, *world_to_picture(frame, wx, wy)) == 1
    placed = MapStreamHandler()
    placed.map_data = tiny
    placed._robot_pixel = (20, 40)
    assert placed.robot_room() == (1, "A")
    placed._robot_pixel = (0, 0)
    assert placed.robot_room() == (1, "A")
    fresh = MapStreamHandler()
    fresh.map_data = tiny
    fresh._robot_pixel = (0, 0)
    assert fresh.robot_room() is None
    quad = picture_rect_to_quad(frame, 0.4, 0.4, 0.6, 0.6)
    assert quad[0][0] < quad[1][0] and quad[0][1] < quad[2][1]

    zone_cmd = build_set_zones_command(
        [((1, 2), (3, 4))],
        [[(0, 0), (10, 0), (10, 10), (0, 10)]],
        [[(20, 20), (30, 20), (30, 40), (20, 40)]],
        map_id=7,
        releases=3,
    )
    raw_zone = base64.b64decode(zone_cmd["170"])
    length, pos = decode_varint(raw_zone, 0)
    assert length == len(raw_zone) - pos
    edit = map_edit_pb2.MapEditRequest()
    edit.ParseFromString(raw_zone[pos:])
    assert edit.method == map_edit_pb2.MapEditRequest.SET_RESTRICTED_ZONES
    assert edit.restricted_zone.virtual_walls[0].p0.x == 1
    assert edit.restricted_zone.virtual_walls[0].p1.y == 4
    assert edit.restricted_zone.forbidden_zones[0].p2.x == 10
    assert edit.restricted_zone.ban_mop_zones[0].p2.y == 40
    assert edit.map_id == 7
    from eufy_clean.api.proto_utils import (
        decode_dnd,
        encode_dnd,
        encode_room_clean_command,
        encode_rooms_custom_command,
    )

    custom_raw = base64.b64decode(
        encode_rooms_custom_command([{"id": 2, "suction": "max", "mop": "low"}], map_id=7)
    )
    clen, cpos = decode_varint(custom_raw, 0)
    custom = map_edit_pb2.MapEditRequest()
    custom.ParseFromString(custom_raw[cpos:])
    assert custom.method == map_edit_pb2.MapEditRequest.SET_ROOMS_CUSTOM
    parm = custom.rooms_custom.rooms_parm.rooms[0]
    assert parm.id == 2
    assert parm.custom.fan.suction == 3
    assert parm.custom.mop_mode.level == 0
    room_raw = base64.b64decode(encode_room_clean_command([2], customize=True))
    rlen, rpos = decode_varint(room_raw, 0)
    room_req = control_pb2.ModeCtrlRequest()
    room_req.ParseFromString(room_raw[rpos:])
    assert room_req.select_rooms_clean.mode == control_pb2.SelectRoomsClean.CUSTOMIZE
    assert room_req.select_rooms_clean.rooms[0].order == 1
    schedule = decode_dnd(encode_dnd(True, 22, 8, 30, 15))
    assert schedule["enabled"] is True
    assert (schedule["start_hour"], schedule["start_minute"]) == (22, 30)
    assert (schedule["end_hour"], schedule["end_minute"]) == (8, 15)
    print("ok", live.robot_pixel, "dock", live._dock_pixel)


if __name__ == "__main__":
    main()
