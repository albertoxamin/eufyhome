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
        value = int.from_bytes(data[pos:pos+8], 'little')
        pos += 8
    elif wire_type == 2:  # Length-delimited
        length, pos = decode_varint(data, pos)
        value = data[pos:pos+length]
        pos += length
    elif wire_type == 5:  # 32-bit
        value = int.from_bytes(data[pos:pos+4], 'little')
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
                        result["mode"] = WORK_MODE_MAP.get(mode_value, f"mode_{mode_value}")
            
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
                        field_num, wire_type, field_value, pos = decode_protobuf_field(data, pos)
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
        if re.match(r'^[A-Za-z0-9+/]*={0,2}$', value):
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
            value_bytes = value.encode('utf-8')
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
    message = b''
    
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
CONTROL_START_SPOT_CLEAN = 3
CONTROL_START_GOHOME = 6
CONTROL_STOP_TASK = 12
CONTROL_PAUSE_TASK = 13
CONTROL_RESUME_TASK = 14

# Clean type constants
CLEAN_TYPE_SWEEP_ONLY = 0
CLEAN_TYPE_MOP_ONLY = 1
CLEAN_TYPE_SWEEP_AND_MOP = 2
CLEAN_TYPE_SWEEP_THEN_MOP = 3

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
    clean_param = b''
    
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
