import json

from custom_components.eufy_clean.api.map_stream import (
    MapStreamHandler,
    try_extract_path_points,
)
from custom_components.eufy_clean.api.proto_utils import encode_varint
from custom_components.eufy_clean.proto.cloud import stream_pb2

WIDTH = 199
HEIGHT = 254
ORIGIN = (-330, -125)
MAP_CHANNEL = 2
PATH_CHANNEL = 11
POSE_CHANNEL = 7
PATH_FRAMES = ["08088089804410E00E", "0908808980C40110E00E"]
POSE_FRAMES = ["0A0A0808A2031023188E04", "0A0A0808FC02102918E504"]


def _lz4_literals(raw: bytes) -> bytes:
    length = len(raw)
    token = bytes([min(length, 15) << 4])
    if length < 15:
        return token + raw
    extra = length - 15
    return token + b"\xff" * (extra // 255) + bytes([extra % 255]) + raw


def _pixels() -> bytes:
    state = 0
    out = bytearray()
    for _ in range(WIDTH * HEIGHT):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((state >> 16) % 4 if (state >> 8) % 7 == 0 else 2)
    return bytes(out)


def _frame(message) -> str:
    body = message.SerializeToString()
    return (encode_varint(len(body)) + body).hex().upper()


def _map_frame() -> str:
    raw = _pixels()
    message = stream_pb2.Map()
    message.pixels = _lz4_literals(raw)
    message.pixel_size = len(raw)
    message.info.map_id = 1
    message.info.width = WIDTH
    message.info.height = HEIGHT
    message.info.resolution = 5
    message.info.origin.x, message.info.origin.y = ORIGIN
    return _frame(message)


def _biz(channel: int, hex_data: str) -> bytes:
    return json.dumps(
        {"payload": {"data": {"channel_id": channel, "data": hex_data}}}
    ).encode()


def _metadata_frame() -> str:
    message = stream_pb2.Metadata()
    message.chan_ids.path = PATH_CHANNEL
    message.chan_ids.dynamic_data = POSE_CHANNEL
    message.chan_ids.map_data = MAP_CHANNEL
    return _frame(message)


def test_map_frame_is_decoded_when_path_parser_also_matches_it():
    frame = _map_frame()
    assert try_extract_path_points(frame)
    handler = MapStreamHandler()

    handler.handle_biz_payload(_biz(MAP_CHANNEL, frame))

    assert handler.map_data is not None
    assert handler.map_channel_id == MAP_CHANNEL


def test_pose_after_stored_path_points_places_the_robot():
    handler = MapStreamHandler()
    for frame in PATH_FRAMES:
        handler.handle_biz_payload(_biz(PATH_CHANNEL, frame))
    handler.handle_biz_payload(_biz(MAP_CHANNEL, _metadata_frame()))
    handler.handle_biz_payload(_biz(MAP_CHANNEL, _map_frame()))
    assert handler.map_data is not None

    for frame in POSE_FRAMES:
        handler.handle_biz_payload(_biz(POSE_CHANNEL, frame))

    assert handler.robot_pixel is not None
