"""Protobuf utilities for decoding Eufy Clean API responses."""

from __future__ import annotations

import base64
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Work status state mapping (from proto enum)
WORK_STATUS_STATE_MAP = {
    0: "standby",
    1: "sleep",
    2: "fault",
    3: "charging",
    4: "fast_mapping",
    5: "cleaning",
    6: "remote_ctrl",
    7: "go_home",
    8: "cruising",
}

# Work mode mapping (from proto enum)
WORK_MODE_MAP = {
    0: "auto",
    1: "select_room",
    2: "select_zone",
    3: "spot",
    4: "fast_mapping",
    5: "global_cruise",
    6: "zones_cruise",
    7: "point_cruise",
    8: "scene",
    9: "smart_follow",
}


def decode_varint(data: bytes, pos: int = 0) -> tuple[int, int]:
    """Decode a varint from bytes, return (value, new_position)."""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def decode_protobuf_field(data: bytes, pos: int = 0) -> tuple[int, int, Any, int]:
    """
    Decode a single protobuf field.
    Returns (field_number, wire_type, value, new_position).
    """
    if pos >= len(data):
        return None, None, None, pos

    tag, pos = decode_varint(data, pos)
    field_number = tag >> 3
    wire_type = tag & 0x07

    value = None

    if wire_type == 0:  # Varint
        value, pos = decode_varint(data, pos)
    elif wire_type == 1:  # 64-bit
        value = int.from_bytes(data[pos : pos + 8], "little")
        pos += 8
    elif wire_type == 2:  # Length-delimited
        length, pos = decode_varint(data, pos)
        value = data[pos : pos + length]
        pos += length
    elif wire_type == 5:  # 32-bit
        value = int.from_bytes(data[pos : pos + 4], "little")
        pos += 4

    return field_number, wire_type, value, pos


def decode_work_status(base64_value: str) -> dict[str, Any]:
    """
    Decode work status protobuf message.

    WorkStatus message structure:
    - field 1: Mode (message with field 1 as enum)
    - field 2: State (enum)
    - field 3: Charging (message)
    - field 6: Cleaning (message)
    - field 7: GoWash (message)
    - field 8: GoHome (message)
    """
    try:
        # Handle length-delimited format (first byte is length)
        data = base64.b64decode(base64_value)

        if len(data) == 0:
            return {"state": "unknown", "mode": "unknown"}

        # Check if first byte is the length (delimited format)
        if data[0] == len(data) - 1:
            data = data[1:]  # Skip the length byte

        result = {
            "state": "unknown",
            "mode": "unknown",
            "charging": None,
            "cleaning": None,
            "go_home": None,
        }

        pos = 0
        while pos < len(data):
            field_num, wire_type, value, pos = decode_protobuf_field(data, pos)

            if field_num is None:
                break

            if field_num == 1 and wire_type == 2:  # Mode message
                # Decode nested Mode message
                mode_data = value
                if len(mode_data) >= 2:
                    # Field 1 in Mode is the enum value
                    _, _, mode_value, _ = decode_protobuf_field(mode_data, 0)
                    if mode_value is not None:
                        result["mode"] = WORK_MODE_MAP.get(
                            mode_value, f"mode_{mode_value}"
                        )

            elif field_num == 2 and wire_type == 0:  # State enum
                result["state"] = WORK_STATUS_STATE_MAP.get(value, f"state_{value}")

            elif field_num == 3 and wire_type == 2:  # Charging message
                result["charging"] = True

            elif field_num == 6 and wire_type == 2:  # Cleaning message
                result["cleaning"] = True

            elif field_num == 8 and wire_type == 2:  # GoHome message
                result["go_home"] = True

        return result

    except Exception as err:
        _LOGGER.debug("Error decoding work status: %s (value: %s)", err, base64_value)
        return {"state": "unknown", "mode": "unknown"}


def decode_error_code(base64_value: str) -> dict[str, Any]:
    """
    Decode error code protobuf message.

    ErrorCode message structure:
    - field 1: last_time (uint64)
    - field 2: error (repeated uint32)
    - field 3: warn (repeated uint32)
    """
    try:
        data = base64.b64decode(base64_value)

        if len(data) == 0:
            return {"errors": [], "warnings": [], "error_text": "none"}

        # Check if first byte is the length (delimited format)
        if data[0] == len(data) - 1:
            data = data[1:]

        result = {
            "errors": [],
            "warnings": [],
            "error_text": "none",
        }

        pos = 0
        while pos < len(data):
            field_num, wire_type, value, pos = decode_protobuf_field(data, pos)

            if field_num is None:
                break

            if field_num == 2:  # error field
                if wire_type == 0:  # Single varint
                    result["errors"].append(value)
                elif wire_type == 2:  # Packed repeated
                    # Decode packed varints
                    inner_pos = 0
                    while inner_pos < len(value):
                        err_val, inner_pos = decode_varint(value, inner_pos)
                        result["errors"].append(err_val)

            elif field_num == 3:  # warn field
                if wire_type == 0:  # Single varint
                    result["warnings"].append(value)
                elif wire_type == 2:  # Packed repeated
                    inner_pos = 0
                    while inner_pos < len(value):
                        warn_val, inner_pos = decode_varint(value, inner_pos)
                        result["warnings"].append(warn_val)

        # Generate error text
        if result["errors"]:
            result["error_text"] = f"error_{result['errors'][0]}"
        elif result["warnings"]:
            result["error_text"] = f"warning_{result['warnings'][0]}"
        else:
            result["error_text"] = "none"

        return result

    except Exception as err:
        _LOGGER.debug("Error decoding error code: %s (value: %s)", err, base64_value)
        return {"errors": [], "warnings": [], "error_text": "none"}


def decode_clean_speed(value: Any) -> str:
    """Decode clean speed value."""
    speed_map = {
        0: "quiet",
        1: "standard",
        2: "turbo",
        3: "max",
    }

    if isinstance(value, int):
        return speed_map.get(value, "standard")

    if isinstance(value, str):
        # Check if it's a single digit
        if len(value) == 1 and value.isdigit():
            return speed_map.get(int(value), "standard")

        # Check if it's base64 encoded
        try:
            if "=" in value or len(value) > 4:
                data = base64.b64decode(value)
                if len(data) >= 1:
                    # Try to extract speed value
                    if data[0] == len(data) - 1 and len(data) > 1:
                        data = data[1:]

                    pos = 0
                    while pos < len(data):
                        field_num, wire_type, field_value, pos = decode_protobuf_field(
                            data, pos
                        )
                        if field_num == 1 and wire_type == 0:
                            return speed_map.get(field_value, "standard")

                    # Fallback: use first byte
                    return speed_map.get(data[0], "standard")
        except Exception:
            pass

        # Return as lowercase string
        return value.lower()

    return "standard"


def is_base64_encoded(value: str) -> bool:
    """Check if a string appears to be base64 encoded."""
    if not isinstance(value, str):
        return False

    # Check for base64 characteristics
    if len(value) < 4:
        return False

    # Check for padding or typical base64 characters
    try:
        # If it decodes successfully and has non-printable chars, likely base64
        decoded = base64.b64decode(value)
        # Check if original string only has base64 chars
        import re

        if re.match(r"^[A-Za-z0-9+/]*={0,2}$", value):
            return True
    except Exception:
        pass

    return False


def encode_varint(value: int) -> bytes:
    """Encode an integer as a varint."""
    result = []
    while value > 127:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def encode_protobuf_field(field_number: int, wire_type: int, value: Any) -> bytes:
    """
    Encode a single protobuf field.
    wire_type: 0 = varint, 2 = length-delimited
    """
    tag = (field_number << 3) | wire_type
    result = encode_varint(tag)

    if wire_type == 0:  # Varint
        result += encode_varint(value)
    elif wire_type == 2:  # Length-delimited
        if isinstance(value, bytes):
            result += encode_varint(len(value))
            result += value
        elif isinstance(value, str):
            value_bytes = value.encode("utf-8")
            result += encode_varint(len(value_bytes))
            result += value_bytes

    return result


def encode_control_command(method: int, params: dict[str, Any] | None = None) -> str:
    """
    Encode a ModeCtrlRequest protobuf message.

    ModeCtrlRequest structure:
    - field 1: method (enum/varint)
    - field 2: seq (uint32) - optional
    - field 3: auto_clean (message) - for START_AUTO_CLEAN

    Methods:
    - 0: START_AUTO_CLEAN
    - 1: START_SELECT_ROOMS_CLEAN
    - 3: START_SPOT_CLEAN
    - 6: START_GOHOME
    - 12: STOP_TASK
    - 13: PAUSE_TASK
    - 14: RESUME_TASK
    """
    # Build the message
    message = b""

    # Field 1: method (varint)
    message += encode_protobuf_field(1, 0, method)

    # For START_AUTO_CLEAN, add auto_clean message with clean_times = 1
    if method == 0 and params:
        # AutoClean message: field 1 = clean_times
        auto_clean_msg = encode_protobuf_field(1, 0, params.get("clean_times", 1))
        message += encode_protobuf_field(3, 2, auto_clean_msg)

    # Add length prefix (delimited format)
    result = encode_varint(len(message)) + message

    return base64.b64encode(result).decode()


def encode_clean_speed_command(speed_index: int) -> str:
    """
    Encode a clean speed command.
    For novel API, this is just the speed index as a varint.
    """
    # Simple encoding - just the value
    return str(speed_index)


# Control method constants
CONTROL_START_AUTO_CLEAN = 0
CONTROL_START_SELECT_ROOMS_CLEAN = 1
CONTROL_START_SELECT_ZONES_CLEAN = 2
CONTROL_START_SPOT_CLEAN = 3
CONTROL_START_GOHOME = 6
CONTROL_STOP_TASK = 12
CONTROL_PAUSE_TASK = 13
CONTROL_RESUME_TASK = 14
CONTROL_START_SCENE_CLEAN = 24


def encode_room_clean_command(room_ids: list[int], clean_times: int = 1) -> str:
    """
    Encode a SelectRoomsClean command for cleaning specific rooms.

    ModeCtrlRequest with method = START_SELECT_ROOMS_CLEAN (1)
    SelectRoomsClean structure:
    - field 1: rooms (repeated Room message)
      - Room: field 1 = id, field 2 = order
    - field 2: clean_times
    """
    # Build SelectRoomsClean message
    select_rooms = b""

    # Field 1: rooms (repeated)
    for order, room_id in enumerate(room_ids):
        # Build Room message: field 1 = id, field 2 = order
        room_msg = encode_protobuf_field(1, 0, room_id)
        room_msg += encode_protobuf_field(2, 0, order + 1)
        select_rooms += encode_protobuf_field(1, 2, room_msg)

    # Field 2: clean_times
    select_rooms += encode_protobuf_field(2, 0, clean_times)

    # Build ModeCtrlRequest
    message = b""
    # Field 1: method = START_SELECT_ROOMS_CLEAN (1)
    message += encode_protobuf_field(1, 0, CONTROL_START_SELECT_ROOMS_CLEAN)
    # Field 4: select_rooms_clean
    message += encode_protobuf_field(4, 2, select_rooms)

    # Add length prefix (delimited format)
    result = encode_varint(len(message)) + message

    return base64.b64encode(result).decode()


def decode_scene_list(base64_value: str) -> list[dict[str, Any]]:
    """
    Decode SceneResponse protobuf from DPS SCENE_LIST (180).

    SceneResponse structure:
    - field 1: varint (version/type)
    - field 2: varint (config flag)
    - field 3: string (empty)
    - field 4: repeated Scene message
      - field 1: message { field 1: scene_id (varint, creation timestamp) }
      - field 3: enabled (varint, 1=active)
      - field 4: name (string, UTF-8)
      - field 5: varint (flag)

    Returns list of {"scene_id": int, "name": str, "enabled": bool}.
    """
    if not base64_value or not isinstance(base64_value, str):
        return []
    try:
        data = base64.b64decode(base64_value)
        if len(data) < 2:
            return []

        # Strip length prefix
        length, pos_after = decode_varint(data, 0)
        if 0 < length == len(data) - pos_after:
            data = data[pos_after:]

        scenes: list[dict[str, Any]] = []
        pos = 0
        while pos < len(data):
            field_num, wire_type, value, pos = decode_protobuf_field(data, pos)
            if field_num is None:
                break
            if field_num == 4 and wire_type == 2 and isinstance(value, bytes):
                scene = _decode_single_scene(value)
                if scene:
                    scenes.append(scene)

        return scenes
    except Exception as err:
        _LOGGER.debug("Error decoding scene list: %s", err)
        return []


def _decode_single_scene(data: bytes) -> dict[str, Any] | None:
    """Decode a single Scene message from the repeated field 4 entries."""
    scene_id = None
    name = ""
    enabled = True

    pos = 0
    while pos < len(data):
        field_num, wire_type, value, pos = decode_protobuf_field(data, pos)
        if field_num is None:
            break
        if field_num == 1 and wire_type == 2 and isinstance(value, bytes):
            # Nested message with field 1 = scene_id
            inner_fn, inner_wt, inner_val, _ = decode_protobuf_field(value, 0)
            if inner_fn == 1 and inner_wt == 0:
                scene_id = inner_val
        elif field_num == 3 and wire_type == 0:
            enabled = value != 0
        elif field_num == 4 and wire_type == 2 and isinstance(value, bytes):
            try:
                name = value.decode("utf-8")
            except UnicodeDecodeError:
                name = ""

    if scene_id is not None and name:
        return {"scene_id": scene_id, "name": name, "enabled": enabled}
    return None


def encode_scene_clean_command(scene_id: int) -> str:
    """
    Encode a ModeCtrlRequest to start a scene clean.

    ModeCtrlRequest structure:
    - field 1: method = START_SCENE_CLEAN (24)
    - field 14: SceneClean { field 1: scene_id }

    The oneof param fields are numbered sequentially (3-13 for methods 0-10,
    14 for SceneClean) regardless of the method enum value.
    """
    scene_clean_msg = encode_protobuf_field(1, 0, scene_id)

    message = b""
    message += encode_protobuf_field(1, 0, CONTROL_START_SCENE_CLEAN)
    message += encode_protobuf_field(14, 2, scene_clean_msg)

    result = encode_varint(len(message)) + message
    return base64.b64encode(result).decode()


def decode_dnd(base64_value: str) -> dict[str, Any]:
    """
    Decode DND (Do Not Disturb) protobuf from DPS 157.

    Structure:
    - field 2 (message):
      - field 1 (message): { field 1: enabled (varint, 1=on, 0=off) }
      - field 2 (message): { field 1: start_hour (varint) }
      - field 3 (message): { field 1: end_hour (varint) }

    Returns {"enabled": bool, "start_hour": int, "end_hour": int}.
    """
    result = {"enabled": False, "start_hour": 22, "end_hour": 8}
    if not base64_value or not isinstance(base64_value, str):
        return result
    try:
        data = base64.b64decode(base64_value)
        if len(data) < 2:
            return result

        # Strip length prefix
        length, pos_after = decode_varint(data, 0)
        if 0 < length == len(data) - pos_after:
            data = data[pos_after:]

        pos = 0
        while pos < len(data):
            field_num, wire_type, value, pos = decode_protobuf_field(data, pos)
            if field_num is None:
                break
            if field_num == 2 and wire_type == 2 and isinstance(value, bytes):
                # Parse the DND schedule message
                inner_pos = 0
                while inner_pos < len(value):
                    f, wt, v, inner_pos = decode_protobuf_field(value, inner_pos)
                    if f is None:
                        break
                    if f == 1 and wt == 2 and isinstance(v, bytes):
                        vf, _, vv, _ = decode_protobuf_field(v, 0)
                        if vf == 1:
                            result["enabled"] = vv != 0
                    elif f == 2 and wt == 2 and isinstance(v, bytes):
                        vf, _, vv, _ = decode_protobuf_field(v, 0)
                        if vf == 1:
                            result["start_hour"] = vv
                    elif f == 3 and wt == 2 and isinstance(v, bytes):
                        vf, _, vv, _ = decode_protobuf_field(v, 0)
                        if vf == 1:
                            result["end_hour"] = vv

        return result
    except Exception as err:
        _LOGGER.debug("Error decoding DND: %s", err)
        return result


def encode_dnd(enabled: bool, start_hour: int, end_hour: int) -> str:
    """
    Encode DND protobuf for DPS 157.

    Mirrors the decoded structure:
    field 1: empty string
    field 2 (message):
      field 1 (message): { field 1: enabled (0 or 1) }
      field 2 (message): { field 1: start_hour }
      field 3 (message): { field 1: end_hour }
    """
    enabled_msg = encode_protobuf_field(1, 0, 1 if enabled else 0)
    start_msg = encode_protobuf_field(1, 0, start_hour)
    end_msg = encode_protobuf_field(1, 0, end_hour)

    schedule = b""
    schedule += encode_protobuf_field(1, 2, enabled_msg)
    schedule += encode_protobuf_field(2, 2, start_msg)
    schedule += encode_protobuf_field(3, 2, end_msg)

    message = b""
    message += encode_protobuf_field(1, 2, b"")  # empty string field 1
    message += encode_protobuf_field(2, 2, schedule)

    result = encode_varint(len(message)) + message
    return base64.b64encode(result).decode()


def decode_cleaning_statistics(base64_value: str) -> dict[str, Any]:
    """
    Decode cleaning statistics protobuf from DPS 167.

    Structure:
    - field 1: { field 1: total_cleans, field 2: type_count }
    - field 2: { field 1: total_area, field 2: total_time_min, field 3: sessions }
    - field 3: { field 1: area2, field 2: time2, field 3: sessions2 }

    Returns {"total_cleans": int, "total_area": int, "total_time_min": int, "total_sessions": int}.
    """
    result = {
        "total_cleans": 0,
        "total_area": 0,
        "total_time_min": 0,
        "total_sessions": 0,
    }
    if not base64_value or not isinstance(base64_value, str):
        return result
    try:
        data = base64.b64decode(base64_value)
        if len(data) < 2:
            return result

        length, pos_after = decode_varint(data, 0)
        if 0 < length == len(data) - pos_after:
            data = data[pos_after:]

        pos = 0
        while pos < len(data):
            field_num, wire_type, value, pos = decode_protobuf_field(data, pos)
            if field_num is None:
                break
            if field_num == 1 and wire_type == 2 and isinstance(value, bytes):
                inner_pos = 0
                while inner_pos < len(value):
                    f, wt, v, inner_pos = decode_protobuf_field(value, inner_pos)
                    if f is None:
                        break
                    if f == 1 and wt == 0:
                        result["total_cleans"] = v
            elif field_num == 2 and wire_type == 2 and isinstance(value, bytes):
                inner_pos = 0
                while inner_pos < len(value):
                    f, wt, v, inner_pos = decode_protobuf_field(value, inner_pos)
                    if f is None:
                        break
                    if f == 1 and wt == 0:
                        result["total_area"] = v
                    elif f == 2 and wt == 0:
                        result["total_time_min"] = v
                    elif f == 3 and wt == 0:
                        result["total_sessions"] = v

        return result
    except Exception as err:
        _LOGGER.debug("Error decoding cleaning statistics: %s", err)
        return result


def decode_consumables(base64_value: str) -> dict[str, Any]:
    """
    Decode consumables/accessories protobuf from DPS 168.

    Structure (field 1 outer message):
    - field 1.1: rolling_brush usage hours
    - field 2.1: side_brush usage hours
    - field 3.1: filter usage hours
    - field 4.1: mop_pad usage hours
    - field 5.1: other_brush usage hours
    - field 6.1: sensor usage hours
    - field 7.1: runtime_hours (total device runtime)

    Returns dict with usage hours for each consumable.
    """
    field_map = {
        1: "rolling_brush",
        2: "side_brush",
        3: "filter",
        4: "mop_pad",
        5: "other_brush",
        6: "sensor",
        7: "runtime_hours",
    }
    result = dict.fromkeys(field_map.values(), 0)
    if not base64_value or not isinstance(base64_value, str):
        return result
    try:
        data = base64.b64decode(base64_value)
        if len(data) < 2:
            return result

        length, pos_after = decode_varint(data, 0)
        if 0 < length == len(data) - pos_after:
            data = data[pos_after:]

        pos = 0
        while pos < len(data):
            field_num, wire_type, value, pos = decode_protobuf_field(data, pos)
            if field_num is None:
                break
            # Outer field 1 contains the consumables message
            if field_num == 1 and wire_type == 2 and isinstance(value, bytes):
                inner_pos = 0
                while inner_pos < len(value):
                    f, wt, v, inner_pos = decode_protobuf_field(value, inner_pos)
                    if f is None:
                        break
                    if f in field_map:
                        if wt == 2 and isinstance(v, bytes):
                            # Nested message: { field 1: value }
                            vf, _, vv, _ = decode_protobuf_field(v, 0)
                            if vf == 1:
                                result[field_map[f]] = vv
                        elif wt == 0:
                            result[field_map[f]] = v
                break  # Only first field 1

        return result
    except Exception as err:
        _LOGGER.debug("Error decoding consumables: %s", err)
        return result


# Clean type constants
CLEAN_TYPE_SWEEP_ONLY = 0
CLEAN_TYPE_MOP_ONLY = 1
CLEAN_TYPE_SWEEP_AND_MOP = 2
CLEAN_TYPE_SWEEP_THEN_MOP = 3


def decode_clean_param(base64_value: str) -> dict[str, Any] | None:
    """
    Decode CleanParamRequest protobuf from DPS CLEANING_PARAMETERS (154).

    Returns dict with clean_type, mop_level, clean_extent, clean_times if present.
    Returns None if decoding fails. If clean_type or mop_level is present, device
    supports clean type (sweep/mop) selection.
    """
    if not base64_value or not isinstance(base64_value, str):
        return None
    try:
        data = base64.b64decode(base64_value)
        if len(data) < 2:
            return None
        result: dict[str, Any] = {}
        # Length prefix (varint) then message, or raw message
        length, pos_after_varint = decode_varint(data, 0)
        if pos_after_varint + length <= len(data):
            message = data[pos_after_varint : pos_after_varint + length]
        else:
            message = data
        pos = 0
        # Parse outer: field 1, type 2 = CleanParam bytes
        while pos < len(message):
            field_num, wire_type, value, pos = decode_protobuf_field(message, pos)
            if field_num is None:
                break
            if field_num == 1 and wire_type == 2 and isinstance(value, bytes):
                inner_pos = 0
                while inner_pos < len(value):
                    f, wt, v, inner_pos = decode_protobuf_field(value, inner_pos)
                    if f is None:
                        break
                    if f == 1 and wt == 0:
                        result["clean_type"] = v
                    elif f == 3 and wt == 2 and isinstance(v, bytes):
                        fi, _, vi, _ = decode_protobuf_field(v, 0)
                        if fi == 1:
                            result["clean_extent"] = vi
                    elif f == 4 and wt == 2 and isinstance(v, bytes):
                        fi, _, vi, _ = decode_protobuf_field(v, 0)
                        if fi == 1:
                            result["mop_level"] = vi
                    elif f == 7 and wt == 0:
                        result["clean_times"] = v
                break
        return result if result else None
    except Exception:
        return None


# Mop level constants
MOP_LEVEL_LOW = 0
MOP_LEVEL_MEDIUM = 1
MOP_LEVEL_HIGH = 2

# Clean extent constants
CLEAN_EXTENT_NORMAL = 0
CLEAN_EXTENT_NARROW = 1  # Deep clean
CLEAN_EXTENT_QUICK = 2


def encode_clean_param(
    clean_type: int | None = None,
    mop_level: int | None = None,
    clean_extent: int | None = None,
    clean_times: int = 1,
) -> str:
    """
    Encode CleanParamRequest protobuf message.

    CleanParamRequest structure:
    - field 1: clean_param (CleanParam message)

    CleanParam structure:
    - field 1: clean_type (CleanType message with field 1 = value enum)
    - field 3: clean_extent (CleanExtent message with field 1 = value enum)
    - field 4: mop_mode (MopMode message with field 1 = level enum)
    - field 7: clean_times (uint32)
    """
    # Build CleanParam message
    clean_param = b""

    # Field 1: clean_type
    if clean_type is not None:
        # CleanType message: field 1 = value (enum)
        clean_type_msg = encode_protobuf_field(1, 0, clean_type)
        clean_param += encode_protobuf_field(1, 2, clean_type_msg)

    # Field 3: clean_extent
    if clean_extent is not None:
        clean_extent_msg = encode_protobuf_field(1, 0, clean_extent)
        clean_param += encode_protobuf_field(3, 2, clean_extent_msg)

    # Field 4: mop_mode
    if mop_level is not None:
        mop_mode_msg = encode_protobuf_field(1, 0, mop_level)
        clean_param += encode_protobuf_field(4, 2, mop_mode_msg)

    # Field 7: clean_times
    clean_param += encode_protobuf_field(7, 0, clean_times)

    # Build CleanParamRequest message
    message = encode_protobuf_field(1, 2, clean_param)

    # Add length prefix (delimited format)
    result = encode_varint(len(message)) + message

    return base64.b64encode(result).decode()
