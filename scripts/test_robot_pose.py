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
    print("ok", live.robot_pixel, "dock", live._dock_pixel)


if __name__ == "__main__":
    main()
