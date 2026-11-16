import json
from types import SimpleNamespace

from test_map_stream import (
    C10_PATH_POINTS,
    MAP_CHANNEL,
    PATH_CHANNEL,
    PATH_CHUNK,
    POSE_CHANNEL,
    POSE_FRAMES,
    _biz,
    _chunk,
    _map_frame,
)

from custom_components.eufy_clean.api.controllers import MqttDevice
from custom_components.eufy_clean.api.proto_utils import encode_protobuf_message
from custom_components.eufy_clean.proto.cloud.work_status_pb2 import WorkStatus

DEVICE = {"device_id": "DEV", "device_model": "T2292", "api_type": "novel"}
STATUS_TOPIC = "cmd/eufy_home/T2292/DEV/res"
BIZ_TOPIC = "biz/eufy_home/T2292/DEV/res"


def _device() -> MqttDevice:
    device = MqttDevice(DEVICE, {}, "", {}, session=None)
    _send(device, BIZ_TOPIC, _biz(MAP_CHANNEL, _map_frame()))
    return device


def _send(device: MqttDevice, topic: str, payload: bytes) -> None:
    device._on_message(None, None, SimpleNamespace(topic=topic, payload=payload))


def _status(device: MqttDevice, state: str) -> None:
    work_status = encode_protobuf_message(
        WorkStatus(state=WorkStatus.State.Value(state))
    )
    _send(
        device,
        STATUS_TOPIC,
        json.dumps({"payload": {"data": {"153": work_status}}}).encode(),
    )


def test_trip_to_the_dock_mid_clean_keeps_the_robot_path():
    device = _device()
    _status(device, "CLEANING")
    _send(device, BIZ_TOPIC, _chunk(PATH_CHANNEL, 0, PATH_CHUNK))

    _status(device, "GO_HOME")
    _status(device, "CLEANING")

    assert device._map_handler._cleaning_path == C10_PATH_POINTS


def test_new_clean_from_the_dock_clears_the_trail_without_a_path_stream():
    device = _device()
    _status(device, "CLEANING")
    for frame in POSE_FRAMES:
        _send(device, BIZ_TOPIC, _biz(POSE_CHANNEL, frame, offset=0))
    assert device._map_handler._robot_trail

    _status(device, "CHARGING")
    _status(device, "CLEANING")

    assert device._map_handler._robot_trail == []
