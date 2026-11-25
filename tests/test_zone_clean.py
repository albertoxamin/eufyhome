import asyncio
import base64
from types import SimpleNamespace

from test_map_stream import MAP_CHANNEL, _biz, _map_frame

from custom_components.eufy_clean.api.controllers import MqttDevice
from custom_components.eufy_clean.api.proto_utils import (
    CONTROL_START_SELECT_ZONES_CLEAN,
    encode_zone_clean_command,
    encode_zones_clean_command,
)
from custom_components.eufy_clean.proto.cloud import control_pb2

DEVICE = {"device_id": "DEV", "device_model": "T2292", "api_type": "novel"}

QUAD_A = [(0, 0), (10, 0), (10, 10), (0, 10)]
QUAD_B = [(20, 20), (30, 20), (30, 30), (20, 30)]


def _decode(encoded: str) -> control_pb2.ModeCtrlRequest:
    raw = base64.b64decode(encoded)
    # encode_protobuf_message prefixes the message with a varint length.
    index = 0
    while raw[index] & 0x80:
        index += 1
    request = control_pb2.ModeCtrlRequest()
    request.ParseFromString(raw[index + 1 :])
    return request


def _corners(zone) -> list[tuple[int, int]]:
    quad = zone.quadrangle
    return [(p.x, p.y) for p in (quad.p0, quad.p1, quad.p2, quad.p3)]


def test_encode_zones_clean_command_carries_every_zone():
    request = _decode(
        encode_zones_clean_command([QUAD_A, QUAD_B], [1, 2], map_id=7, releases=3)
    )
    assert request.method == CONTROL_START_SELECT_ZONES_CLEAN
    zones = request.select_zones_clean.zones
    assert len(zones) == 2
    assert _corners(zones[0]) == QUAD_A
    assert _corners(zones[1]) == QUAD_B
    assert [zone.clean_times for zone in zones] == [1, 2]
    assert request.select_zones_clean.map_id == 7
    assert request.select_zones_clean.releases == 3


def test_encode_zones_clean_command_repeats_one_pass_count():
    request = _decode(encode_zones_clean_command([QUAD_A, QUAD_B], 2))
    assert [zone.clean_times for zone in request.select_zones_clean.zones] == [2, 2]


def test_encode_zone_clean_command_still_encodes_one_zone():
    request = _decode(encode_zone_clean_command(QUAD_A, 2))
    assert request.method == CONTROL_START_SELECT_ZONES_CLEAN
    zones = request.select_zones_clean.zones
    assert len(zones) == 1
    assert _corners(zones[0]) == QUAD_A
    assert zones[0].clean_times == 2


def _device_with_map() -> MqttDevice:
    device = MqttDevice(DEVICE, {}, "", {}, session=None)
    device._on_message(
        None,
        None,
        SimpleNamespace(
            topic="biz/eufy_home/T2292/DEV/res",
            payload=_biz(MAP_CHANNEL, _map_frame()),
        ),
    )
    assert device._map_handler.map_frame is not None
    return device


def test_clean_area_sends_all_areas_in_one_command():
    device = _device_with_map()
    sent: list[dict] = []

    async def fake_send(data):
        sent.append(data)

    device.send_command = fake_send
    asyncio.run(
        device.clean_area(
            0.1,
            0.1,
            0.2,
            0.2,
            clean_times=1,
            suction="standard",
            mop="medium",
            kind="both",
            areas=[(0.1, 0.1, 0.2, 0.2), (0.5, 0.5, 0.6, 0.6)],
        )
    )
    zone_commands = [data["152"] for data in sent if "152" in data]
    assert len(zone_commands) == 1
    request = _decode(zone_commands[0])
    assert request.method == CONTROL_START_SELECT_ZONES_CLEAN
    assert len(request.select_zones_clean.zones) == 2
