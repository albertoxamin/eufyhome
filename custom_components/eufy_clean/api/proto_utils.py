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
