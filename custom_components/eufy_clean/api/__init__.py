"""Eufy Clean API."""

from .controllers import CloudDevice, MqttDevice
from .eufy_api import EufyCleanApi
from .proto_utils import (
    CONTROL_START_SCENE_CLEAN,
    decode_clean_speed,
    decode_cleaning_statistics,
    decode_consumables,
    decode_dnd,
    decode_error_code,
    decode_scene_list,
    decode_work_status,
    encode_clean_param,
    encode_control_command,
    encode_dnd,
    encode_room_clean_command,
    encode_scene_clean_command,
)

__all__ = [
    "EufyCleanApi",
    "CloudDevice",
    "MqttDevice",
    "decode_work_status",
    "decode_error_code",
    "decode_clean_speed",
    "decode_scene_list",
    "decode_dnd",
    "decode_cleaning_statistics",
    "decode_consumables",
    "encode_control_command",
    "encode_clean_param",
    "encode_room_clean_command",
    "encode_scene_clean_command",
    "encode_dnd",
    "CONTROL_START_SCENE_CLEAN",
]
