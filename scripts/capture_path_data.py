#!/usr/bin/env python3
"""
Capture DPS 165 (map) and DPS 166 (path) data from Eufy robot.

SAFE: This script is LISTEN-ONLY.
- Fetches device data via the HTTP API (read-only)
- Connects to MQTT and subscribes, but NEVER publishes/sends commands
- Saves raw base64 + decoded bytes to files for analysis
- Attempts to decode path data as protobuf coordinate list

Run from repo root:
  python scripts/capture_path_data.py
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import os
import ssl
import struct
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# How long to listen for MQTT messages (seconds)
LISTEN_SECONDS = 300

# DPS keys we care about (map, path, map management)
PATH_DPS_KEYS = {"164", "165", "166", "170", "171", "172"}

OUTPUT_DIR = REPO_ROOT / "scripts" / "captured_data"

received_dps: dict[str, list] = {}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_credentials() -> tuple[str, str]:
    creds_file = REPO_ROOT / "test_credentials.env"
    if creds_file.exists():
        for line in creds_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key == "EUFY_USERNAME":
                    os.environ.setdefault("EUFY_USERNAME", value)
                elif key == "EUFY_PASSWORD":
                    os.environ.setdefault("EUFY_PASSWORD", value)

    username = os.environ.get("EUFY_USERNAME", "").strip()
    password = os.environ.get("EUFY_PASSWORD", "").strip()
    if not username or not password:
        print("Missing credentials. Set EUFY_USERNAME and EUFY_PASSWORD.")
        sys.exit(1)
    return username, password


def decode_protobuf_recursive(data: bytes, depth: int = 0) -> list[dict]:
    """Recursively decode protobuf fields for analysis."""
    from eufy_clean.api.proto_utils import decode_protobuf_field, decode_varint

    fields = []
    pos = 0

    # Try stripping length prefix
    if len(data) >= 2:
        ln, pos_after = decode_varint(data, 0)
        if 0 < ln <= len(data) - pos_after and ln == len(data) - pos_after:
            data = data[pos_after : pos_after + ln]
            pos = 0

    while pos < len(data):
        field_num, wire_type, value, new_pos = decode_protobuf_field(data, pos)
        if field_num is None:
            break
        pos = new_pos

        field = {"field": field_num, "wire_type": wire_type}

        if wire_type == 0:  # varint
            field["type"] = "varint"
            field["value"] = value
        elif wire_type == 1:  # fixed64
            field["type"] = "fixed64"
            field["value"] = value
            # Also try interpreting as double
            try:
                field["as_double"] = struct.unpack("<d", struct.pack("<Q", value))[0]
            except Exception:
                pass
        elif wire_type == 5:  # fixed32
            field["type"] = "fixed32"
            field["value"] = value
            # Also try interpreting as float
            try:
                field["as_float"] = struct.unpack("<f", struct.pack("<I", value))[0]
            except Exception:
                pass
        elif wire_type == 2:  # length-delimited (bytes)
            field["type"] = "bytes"
            field["length"] = len(value)
            if depth < 3 and len(value) > 2:
                # Try to decode as nested protobuf
                try:
                    nested = decode_protobuf_recursive(value, depth + 1)
                    if nested and len(nested) > 0:
                        field["nested"] = nested
                except Exception:
                    pass
            # Also try decoding as UTF-8 string
            try:
                text = value.decode("utf-8")
                if text.isprintable():
                    field["as_string"] = text
            except Exception:
                pass
            # Show hex preview
            field["hex_preview"] = value[:64].hex()

        fields.append(field)

    return fields


def try_decode_path(raw_b64: str) -> dict:
    """Try various approaches to decode path data."""
    from eufy_clean.api.proto_utils import decode_protobuf_field, decode_varint

    data = base64.b64decode(raw_b64)
    result = {
        "raw_size": len(data),
        "interpretations": [],
    }

    # 1. Raw protobuf decode
    try:
        fields = decode_protobuf_recursive(data)
        result["protobuf_fields"] = fields
    except Exception as e:
        result["protobuf_error"] = str(e)

    # 2. Collect all varints and blobs (like the map parser does)
    varints: list[int] = []
    blobs: list[bytes] = []

    def collect(msg: bytes) -> None:
        pos = 0
        if len(msg) >= 2:
            ln, pos_after = decode_varint(msg, 0)
            if 0 < ln <= len(msg) - pos_after and ln == len(msg) - pos_after:
                msg = msg[pos_after : pos_after + ln]
        while pos < len(msg):
            field_num, wire_type, value, pos = decode_protobuf_field(msg, pos)
            if field_num is None:
                break
            if wire_type == 0:
                varints.append(value)
            elif wire_type == 2 and isinstance(value, bytes):
                if len(value) > 20:
                    try:
                        collect(value)
                    except Exception:
                        blobs.append(value)
                else:
                    blobs.append(value)

    try:
        collect(data)
        result["varints"] = varints
        result["blob_sizes"] = [len(b) for b in blobs]
    except Exception as e:
        result["collect_error"] = str(e)

    # 3. Try interpreting largest blob as coordinate pairs
    if blobs:
        largest = max(blobs, key=len)

        # Try as raw int16 pairs (x, y)
        if len(largest) >= 4 and len(largest) % 4 == 0:
            try:
                coords_i16 = []
                for i in range(0, len(largest), 4):
                    x = struct.unpack_from("<h", largest, i)[0]
                    y = struct.unpack_from("<h", largest, i + 2)[0]
                    coords_i16.append((x, y))
                if coords_i16:
                    result["interpretations"].append({
                        "format": "int16_pairs_le",
                        "count": len(coords_i16),
                        "first_10": coords_i16[:10],
                        "last_5": coords_i16[-5:],
                    })
            except Exception:
                pass

        # Try as raw uint16 pairs (x, y)
        if len(largest) >= 4 and len(largest) % 4 == 0:
            try:
                coords_u16 = []
                for i in range(0, len(largest), 4):
                    x = struct.unpack_from("<H", largest, i)[0]
                    y = struct.unpack_from("<H", largest, i + 2)[0]
                    coords_u16.append((x, y))
                if coords_u16:
                    result["interpretations"].append({
                        "format": "uint16_pairs_le",
                        "count": len(coords_u16),
                        "first_10": coords_u16[:10],
                        "last_5": coords_u16[-5:],
                    })
            except Exception:
                pass

        # Try as raw uint8 pairs (x, y)
        if len(largest) >= 2 and len(largest) % 2 == 0:
            try:
                coords_u8 = []
                for i in range(0, len(largest), 2):
                    x = largest[i]
                    y = largest[i + 1]
                    coords_u8.append((x, y))
                if coords_u8:
                    result["interpretations"].append({
                        "format": "uint8_pairs",
                        "count": len(coords_u8),
                        "first_10": coords_u8[:10],
                        "last_5": coords_u8[-5:],
                    })
            except Exception:
                pass

        # Try LZ4 decompression first, then coordinates
        try:
            import lz4.block

            for target_size in (
                len(largest) * 2,
                len(largest) * 4,
                len(largest) * 8,
                256 * 256,
                512 * 512,
            ):
                try:
                    decompressed = lz4.block.decompress(
                        largest, uncompressed_size=target_size
                    )
                    if decompressed:
                        result["interpretations"].append({
                            "format": f"lz4_decompressed",
                            "compressed_size": len(largest),
                            "decompressed_size": len(decompressed),
                            "target_size": target_size,
                            "hex_preview": decompressed[:64].hex(),
                        })
                        break
                except Exception:
                    continue
        except ImportError:
            pass

    # 4. Try interpreting varints as coordinate pairs
    if len(varints) >= 2:
        # Pairs of consecutive varints
        varint_pairs = []
        for i in range(0, len(varints) - 1, 2):
            varint_pairs.append((varints[i], varints[i + 1]))
        if varint_pairs:
            result["interpretations"].append({
                "format": "varint_pairs",
                "count": len(varint_pairs),
                "pairs": varint_pairs[:20],
            })

    return result


def try_render_path_on_map(
    map_b64: str | None, path_b64: str, device_id: str
) -> Path | None:
    """Try to render path data overlaid on the map (if available)."""
    try:
        from PIL import Image, ImageDraw

        from eufy_clean.api.proto_utils import decode_protobuf_field, decode_varint

        # First decode the path data to find coordinates
        path_data = base64.b64decode(path_b64)
        path_info = try_decode_path(path_b64)

        # Look for coordinate-like data in interpretations
        coords = None
        for interp in path_info.get("interpretations", []):
            if interp["format"] in ("int16_pairs_le", "uint16_pairs_le"):
                # Reconstruct full list from first/last samples
                # For actual rendering, we'd need the full list
                # but we stored only previews in try_decode_path
                pass

        # Re-extract coordinates directly from largest blob
        varints: list[int] = []
        blobs: list[bytes] = []

        def collect(msg: bytes) -> None:
            pos = 0
            if len(msg) >= 2:
                ln, pos_after = decode_varint(msg, 0)
                if 0 < ln <= len(msg) - pos_after and ln == len(msg) - pos_after:
                    msg = msg[pos_after : pos_after + ln]
            while pos < len(msg):
                field_num, wire_type, value, pos = decode_protobuf_field(msg, pos)
                if field_num is None:
                    break
                if wire_type == 0:
                    varints.append(value)
                elif wire_type == 2 and isinstance(value, bytes):
                    if len(value) > 20:
                        try:
                            collect(value)
                        except Exception:
                            blobs.append(value)
                    else:
                        blobs.append(value)

        collect(path_data)

        if not blobs:
            print("  No blobs found in path data for rendering")
            return None

        largest = max(blobs, key=len)

        # Try int16 pairs
        if len(largest) >= 4:
            coords = []
            for i in range(0, len(largest) - 3, 4):
                x = struct.unpack_from("<h", largest, i)[0]
                y = struct.unpack_from("<h", largest, i + 2)[0]
                coords.append((x, y))

        if not coords:
            print("  Could not extract coordinates from path data")
            return None

        # Find bounds
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        if max_x == min_x or max_y == min_y:
            print(f"  Path coords are degenerate: x=[{min_x},{max_x}] y=[{min_y},{max_y}]")
            return None

        print(f"  Path coordinates: {len(coords)} points")
        print(f"  Bounds: x=[{min_x},{max_x}] y=[{min_y},{max_y}]")

        # Create standalone path image
        margin = 20
        width = max_x - min_x + 2 * margin
        height = max_y - min_y + 2 * margin

        # Cap size
        scale = 1
        if width > 2000 or height > 2000:
            scale_x = 2000 / width
            scale_y = 2000 / height
            scale = min(scale_x, scale_y)
        elif width < 200 and height < 200:
            scale = max(1, min(800 // max(width, 1), 800 // max(height, 1)))

        img_w = int(width * scale)
        img_h = int(height * scale)
        img = Image.new("RGBA", (img_w, img_h), (240, 240, 240, 255))
        draw = ImageDraw.Draw(img)

        # Draw path as connected line segments
        scaled_coords = [
            (int((x - min_x + margin) * scale), int((y - min_y + margin) * scale))
            for x, y in coords
        ]

        if len(scaled_coords) >= 2:
            # Draw line
            draw.line(scaled_coords, fill=(0, 100, 255, 200), width=max(1, int(scale)))

        # Mark start (green) and end (red)
        if scaled_coords:
            sx, sy = scaled_coords[0]
            draw.ellipse([sx - 4, sy - 4, sx + 4, sy + 4], fill=(0, 200, 0, 255))
            ex, ey = scaled_coords[-1]
            draw.ellipse([ex - 4, ey - 4, ex + 4, ey + 4], fill=(255, 0, 0, 255))

        safe_id = "".join(c if c.isalnum() else "_" for c in device_id)[:32]
        out = OUTPUT_DIR / f"path_{safe_id}.png"
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.write_bytes(buf.getvalue())
        return out

    except Exception as e:
        print(f"  Path render failed: {e}")
        import traceback

        traceback.print_exc()
        return None


def save_dps_data(dps_key: str, raw_b64: str, device_id: str, source: str) -> None:
    """Save captured DPS data to files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c if c.isalnum() else "_" for c in device_id)[:32]
    ts = int(time.time())

    # Save raw base64
    b64_file = OUTPUT_DIR / f"dps{dps_key}_{safe_id}_{source}_{ts}.b64"
    b64_file.write_text(raw_b64)

    # Save decoded bytes
    try:
        raw_bytes = base64.b64decode(raw_b64)
        bin_file = OUTPUT_DIR / f"dps{dps_key}_{safe_id}_{source}_{ts}.bin"
        bin_file.write_bytes(raw_bytes)

        # Save hex dump
        hex_file = OUTPUT_DIR / f"dps{dps_key}_{safe_id}_{source}_{ts}.hex"
        lines = []
        for i in range(0, len(raw_bytes), 16):
            chunk = raw_bytes[i : i + 16]
            hex_part = " ".join(f"{b:02x}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:08x}  {hex_part:<48s}  {ascii_part}")
        hex_file.write_text("\n".join(lines))

        print(f"  Saved DPS {dps_key}: {len(raw_bytes)} bytes ({source})")
        print(f"    {b64_file.name}")
        print(f"    {bin_file.name}")
        print(f"    {hex_file.name}")
    except Exception as e:
        print(f"  Error saving DPS {dps_key}: {e}")


async def main() -> None:
    username, password = load_credentials()

    import types

    sys.modules["eufy_clean"] = types.ModuleType("eufy_clean")
    sys.modules["eufy_clean.api"] = types.ModuleType("eufy_clean.api")
    _load_module("eufy_clean.const", COMPONENT_ROOT / "const.py")
    _load_module("eufy_clean.api.proto_utils", COMPONENT_ROOT / "api" / "proto_utils.py")
    _load_module("eufy_clean.api.eufy_api", COMPONENT_ROOT / "api" / "eufy_api.py")
    from eufy_clean.api.eufy_api import EufyCleanApi

    print("=" * 60)
    print("Eufy Path Data Capture (LISTEN-ONLY, no commands sent)")
    print("=" * 60)

    print("\nLogging in to Eufy...")
    api = EufyCleanApi(username=username, password=password)
    await api.login()

    devices = await api.get_all_devices()
    if not devices:
        print("No devices found.")
        await api.close()
        return

    device = devices[0]
    device_id = device["device_id"]
    device_model = device["device_model"]
    print(f"Device: {device.get('device_name', device_id)} ({device_model})")

    # ----------------------------------------------------------------
    # STEP 1: Check HTTP API for DPS 165/166
    # ----------------------------------------------------------------
    print("\n--- Step 1: Checking HTTP API for existing map/path data ---")
    dps = device.get("dps", {})
    found_via_http = False

    for key in sorted(dps.keys(), key=lambda x: int(x) if x.isdigit() else x):
        value = dps[key]
        if key in PATH_DPS_KEYS and isinstance(value, str) and len(value) > 4:
            raw_size = len(base64.b64decode(value))
            print(f"\n  DPS {key}: {len(value)} chars base64 ({raw_size} bytes decoded)")
            save_dps_data(key, value, device_id, "http")
            found_via_http = True

            # Try decoding
            if key == "166":
                print("\n  Attempting path decode...")
                decoded = try_decode_path(value)
                # Save analysis
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                safe_id = "".join(c if c.isalnum() else "_" for c in device_id)[:32]
                analysis_file = OUTPUT_DIR / f"path_analysis_{safe_id}.json"
                # Convert to JSON-serializable
                analysis_json = json.dumps(decoded, indent=2, default=str)
                analysis_file.write_text(analysis_json)
                print(f"  Analysis saved to: {analysis_file.name}")

                # Print summary
                if decoded.get("protobuf_fields"):
                    print(f"  Protobuf fields: {len(decoded['protobuf_fields'])}")
                    for f in decoded["protobuf_fields"][:10]:
                        print(f"    field {f['field']} ({f['type']}): {f.get('value', f.get('length', '?'))}")
                if decoded.get("varints"):
                    print(f"  Varints: {decoded['varints'][:20]}")
                if decoded.get("blob_sizes"):
                    print(f"  Blob sizes: {decoded['blob_sizes']}")
                for interp in decoded.get("interpretations", []):
                    fmt = interp["format"]
                    count = interp.get("count", "?")
                    print(f"  Interpretation [{fmt}]: {count} items")
                    if "first_10" in interp:
                        print(f"    First 10: {interp['first_10']}")

                # Try rendering
                map_b64 = dps.get("165")
                path_out = try_render_path_on_map(map_b64, value, device_id)
                if path_out:
                    print(f"  Path image saved: {path_out}")

            elif key == "165":
                raw = base64.b64decode(value)
                print(f"  Map data: {raw_size} bytes (saved for reference)")
        elif key in PATH_DPS_KEYS:
            print(f"\n  DPS {key}: present but empty/non-string ({type(value).__name__})")

    if not found_via_http:
        print("  No DPS 165/166 data found in HTTP response.")
        print("  This is normal if the robot hasn't cleaned recently.")
        # Print all DPS keys we DID find
        print(f"  Available DPS keys: {sorted(dps.keys(), key=lambda x: int(x) if x.isdigit() else x)}")

    # ----------------------------------------------------------------
    # STEP 2: Listen on MQTT (NO COMMANDS)
    # ----------------------------------------------------------------
    mqtt_creds = api.mqtt_credentials
    if not mqtt_creds:
        print("\nNo MQTT credentials available, skipping MQTT listen.")
        await api.close()
        return

    print(f"\n--- Step 2: Listening on MQTT for {LISTEN_SECONDS}s (NO commands will be sent) ---")

    import paho.mqtt.client as mqtt

    openudid = api.openudid
    user_id = mqtt_creds.get("user_id", "")
    app_name = mqtt_creds.get("app_name", "eufy_home")
    client_id = f"android-{app_name}-eufy_android_{openudid}_{user_id}"

    topic_res = f"cmd/eufy_home/{device_model}/{device_id}/res"
    topic_req = f"cmd/eufy_home/{device_model}/{device_id}/req"
    topic_smart_in = f"smart/mb/in/{device_id}"
    topic_smart_out = f"smart/mb/out/{device_id}"

    message_count = 0

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0 or str(reason_code) == "Success":
            print(f"  Connected to MQTT broker")
            client.subscribe(topic_res)
            client.subscribe(topic_smart_in)
            print(f"  Subscribed to: {topic_res}")
            print(f"  Subscribed to: {topic_smart_in}")
            print(f"\n  Listening... (will NOT send any commands)")
        else:
            print(f"  MQTT connection failed: rc={reason_code}")

    def on_message(client, userdata, msg):
        nonlocal message_count
        message_count += 1

        try:
            payload = json.loads(msg.payload.decode())
            data = payload.get("payload", {})
            if isinstance(data, str):
                data = json.loads(data)

            dps = data.get("data", {})
            if not dps:
                return

            dps_keys = sorted(dps.keys(), key=lambda x: int(x) if x.isdigit() else x)
            has_target = any(k in PATH_DPS_KEYS for k in dps_keys)
            prefix = ">>>" if has_target else "   "
            # Identify direction
            topic = msg.topic
            if "/req" in topic or "/out/" in topic:
                direction = "APP->DEV"
            else:
                direction = "DEV->APP"
            print(f"\n{prefix} MQTT msg #{message_count} [{direction}]: DPS keys = {dps_keys}")

            for k in dps_keys:
                v = dps[k]
                if v is None:
                    continue

                received_dps.setdefault(k, []).append(v)

                if k in PATH_DPS_KEYS and isinstance(v, str) and len(v) > 4:
                    raw_size = len(base64.b64decode(v))
                    print(f"    *** DPS {k}: {len(v)} chars base64 ({raw_size} bytes)")
                    save_dps_data(k, v, device_id, "mqtt")

                    if k == "166":
                        print("    Attempting path decode...")
                        decoded = try_decode_path(v)
                        for interp in decoded.get("interpretations", []):
                            fmt = interp["format"]
                            count = interp.get("count", "?")
                            print(f"      [{fmt}]: {count} items")
                            if "first_10" in interp:
                                print(f"        First 10: {interp['first_10']}")

                        map_b64 = None
                        if "165" in received_dps:
                            map_b64 = received_dps["165"][-1]
                        path_out = try_render_path_on_map(map_b64, v, device_id)
                        if path_out:
                            print(f"      Path image: {path_out}")
                elif isinstance(v, str) and len(v) > 100:
                    print(f"    DPS {k}: str({len(v)} chars)")
                elif isinstance(v, str):
                    print(f"    DPS {k}: {v}")
                else:
                    print(f"    DPS {k}: {v}")

        except Exception as e:
            print(f"  Parse error: {e}")

    def on_disconnect(client, userdata, flags, reason_code, properties):
        print(f"  Disconnected from MQTT: rc={reason_code}")

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )

    cert_pem = mqtt_creds.get("certificate_pem", "")
    private_key = mqtt_creds.get("private_key", "")

    cert_file = key_file = None
    if cert_pem and private_key:
        cert_file = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
        cert_file.write(cert_pem)
        cert_file.close()

        key_file = tempfile.NamedTemporaryFile(mode="w", suffix=".key", delete=False)
        key_file.write(private_key)
        key_file.close()

        client.tls_set(certfile=cert_file.name, keyfile=key_file.name)
        client.tls_insecure_set(True)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    endpoint = mqtt_creds.get("endpoint_addr", "")
    if not endpoint:
        print("  No MQTT endpoint in credentials.")
        await api.close()
        return

    print(f"  Connecting to {endpoint}:8883 ...")
    try:
        client.connect(endpoint, 8883, keepalive=60)
    except Exception as e:
        print(f"  MQTT connect failed: {e}")
        await api.close()
        return

    client.loop_start()

    # Just listen — NO send_dps_command calls
    try:
        await asyncio.sleep(LISTEN_SECONDS)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    client.loop_stop()
    try:
        client.disconnect()
    except Exception:
        pass
    await api.close()

    # Clean up temp files
    if cert_file:
        try:
            os.unlink(cert_file.name)
        except Exception:
            pass
    if key_file:
        try:
            os.unlink(key_file.name)
        except Exception:
            pass

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Summary: {message_count} MQTT messages received in {LISTEN_SECONDS}s")
    if received_dps:
        print("DPS keys seen via MQTT:")
        for k in sorted(received_dps.keys(), key=lambda x: int(x) if x.isdigit() else x):
            updates = received_dps[k]
            marker = " <-- TARGET" if k in PATH_DPS_KEYS else ""
            sizes = []
            for v in updates:
                if isinstance(v, str):
                    sizes.append(f"{len(v)}ch")
                else:
                    sizes.append(str(v))
            print(f"  DPS {k:>4s}: {len(updates)} update(s) [{', '.join(sizes[:5])}]{marker}")
    else:
        print("No MQTT DPS updates received.")
    print()

    if OUTPUT_DIR.exists():
        files = sorted(OUTPUT_DIR.iterdir())
        if files:
            print(f"Captured files in {OUTPUT_DIR}:")
            for f in files:
                print(f"  {f.name} ({f.stat().st_size} bytes)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
