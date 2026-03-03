#!/usr/bin/env python3
"""
Probe for map-related API endpoints on the Eufy clean API.

SAFE: Read-only HTTP requests only. No MQTT, no commands.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUTPUT_DIR = REPO_ROOT / "scripts" / "captured_data"


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
        print("Missing credentials.")
        sys.exit(1)
    return username, password


async def main() -> None:
    import aiohttp
    import types

    username, password = load_credentials()

    sys.modules["eufy_clean"] = types.ModuleType("eufy_clean")
    sys.modules["eufy_clean.api"] = types.ModuleType("eufy_clean.api")
    _load_module("eufy_clean.const", COMPONENT_ROOT / "const.py")
    _load_module("eufy_clean.api.proto_utils", COMPONENT_ROOT / "api" / "proto_utils.py")
    _load_module("eufy_clean.api.eufy_api", COMPONENT_ROOT / "api" / "eufy_api.py")
    from eufy_clean.api.eufy_api import EufyCleanApi

    print("=" * 60)
    print("Eufy Map API Probe (read-only)")
    print("=" * 60)

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
    print(f"Device ID: {device_id}")

    # ----------------------------------------------------------------
    # STEP 1: Dump ALL DPS from get_device_list (full response)
    # ----------------------------------------------------------------
    print("\n--- Step 1: Full device DPS dump ---")
    dps = device.get("dps", {})
    for key in sorted(dps.keys(), key=lambda x: int(x) if x.isdigit() else x):
        value = dps[key]
        if isinstance(value, str) and len(value) > 100:
            raw_size = 0
            try:
                raw_size = len(base64.b64decode(value))
            except Exception:
                pass
            print(f"  DPS {key:>4s}: str({len(value)} chars, ~{raw_size}B decoded)")
        elif isinstance(value, str):
            print(f"  DPS {key:>4s}: {value!r}")
        else:
            print(f"  DPS {key:>4s}: {value}")

    # ----------------------------------------------------------------
    # STEP 2: Get the raw get_device_list response to see full structure
    # ----------------------------------------------------------------
    print("\n--- Step 2: Raw get_device_list response structure ---")
    session = await api._get_session()
    headers = {
        "user-agent": "EufyHome-Android-3.1.3-753",
        "timezone": "Europe/Berlin",
        "openudid": api.openudid,
        "language": "en",
        "country": "US",
        "os-version": "Android",
        "model-type": "PHONE",
        "app-name": "eufy_home",
        "x-auth-token": api.user_info.get("user_center_token", ""),
        "gtoken": api.user_info.get("gtoken", ""),
        "content-type": "application/json; charset=UTF-8",
    }

    async with session.post(
        "https://aiot-clean-api-pr.eufylife.com/app/devicerelation/get_device_list",
        headers=headers,
        json={"attribute": 3},
    ) as resp:
        result = await resp.json()
        # Save full response
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "get_device_list_full.json").write_text(
            json.dumps(result, indent=2, default=str)
        )
        print(f"  Saved full response to get_device_list_full.json")
        # Print structure (keys at each level)
        data = result.get("data", result)
        if data.get("devices"):
            dev_obj = data["devices"][0]
            print(f"  Device object keys: {list(dev_obj.keys())}")
            dev = dev_obj.get("device", dev_obj)
            print(f"  Inner device keys: {list(dev.keys())}")
            # Check for map-related keys
            for k in dev.keys():
                if "map" in k.lower() or "floor" in k.lower() or "room" in k.lower():
                    print(f"    >>> Map-related key: {k} = {dev[k]!r:.200s}")

    # ----------------------------------------------------------------
    # STEP 3: Probe known API base for map endpoints
    # ----------------------------------------------------------------
    print("\n--- Step 3: Probing map-related API endpoints ---")
    BASE = "https://aiot-clean-api-pr.eufylife.com"

    # Common patterns observed in Eufy/Anker clean APIs
    endpoints_to_try = [
        # Map list/management
        ("/app/map/get_map_list", {"device_sn": device_id}),
        ("/app/map/get_map_list", {"deviceSn": device_id}),
        ("/app/map/get_map_info", {"device_sn": device_id}),
        ("/app/map/getMapList", {"device_sn": device_id}),
        ("/app/map/get_clean_map", {"device_sn": device_id}),
        ("/app/map/get_latest_map", {"device_sn": device_id}),
        # Device manage patterns
        ("/app/devicemanage/get_map_list", {"device_sn": device_id}),
        ("/app/devicemanage/get_map", {"device_sn": device_id}),
        ("/app/devicemanage/get_device_map", {"device_sn": device_id}),
        # Clean record / history patterns
        ("/app/clean/get_clean_record_list", {"device_sn": device_id}),
        ("/app/clean/get_clean_record", {"device_sn": device_id}),
        ("/app/clean/getCleanRecordList", {"device_sn": device_id}),
        ("/app/devicerelation/get_clean_record_list", {"device_sn": device_id}),
        # Floor map patterns
        ("/app/floormap/get_map_list", {"device_sn": device_id}),
        ("/app/floormap/get_floor_list", {"device_sn": device_id}),
        # Robot patterns
        ("/app/robot/get_map_list", {"device_sn": device_id}),
        ("/app/robot/get_map_info", {"device_sn": device_id}),
        # Thing patterns (same base as get_product_data_point)
        ("/app/things/get_device_map", {"device_sn": device_id}),
        ("/app/things/get_map_list", {"device_sn": device_id}),
        # Cloud record
        ("/app/cloudrecord/get_record_list", {"device_sn": device_id}),
        ("/app/cloudrecord/get_clean_map", {"device_sn": device_id}),
        # Additional patterns from other Eufy integrations
        ("/app/devicerelation/get_device_clean_map", {"device_sn": device_id}),
        ("/app/devicerelation/get_device_map_info", {"device_sn": device_id}),
        ("/app/multi_map/get_map_list", {"device_sn": device_id}),
        ("/app/multi_map/get_current_map", {"device_sn": device_id}),
    ]

    found_endpoints = []
    for endpoint, payload in endpoints_to_try:
        try:
            async with session.post(
                f"{BASE}{endpoint}",
                headers=headers,
                json=payload,
            ) as resp:
                status = resp.status
                result = await resp.json()
                code = result.get("code", result.get("res_code", "?"))
                msg = result.get("msg", result.get("message", ""))

                # Check if it's a valid endpoint (not 404 / "not found")
                is_valid = (
                    status == 200
                    and code not in (404, "404", -1, 10001)
                    and "not found" not in str(msg).lower()
                    and "no route" not in str(msg).lower()
                )

                if is_valid:
                    print(f"  [HIT] {endpoint}")
                    print(f"         status={status} code={code} msg={msg!r:.80s}")
                    # Save response
                    safe_name = endpoint.replace("/", "_").strip("_")
                    (OUTPUT_DIR / f"api_{safe_name}.json").write_text(
                        json.dumps(result, indent=2, default=str)
                    )
                    print(f"         Response saved to api_{safe_name}.json")

                    # Print interesting data
                    resp_data = result.get("data", {})
                    if resp_data:
                        if isinstance(resp_data, dict):
                            print(f"         Data keys: {list(resp_data.keys())[:20]}")
                        elif isinstance(resp_data, list):
                            print(f"         Data: list of {len(resp_data)} items")
                            if resp_data:
                                first = resp_data[0]
                                if isinstance(first, dict):
                                    print(f"         First item keys: {list(first.keys())[:20]}")
                    found_endpoints.append(endpoint)
                else:
                    print(f"  [---] {endpoint}  (code={code})")
        except Exception as e:
            print(f"  [ERR] {endpoint}  ({e})")

    # ----------------------------------------------------------------
    # STEP 4: Also try GET requests
    # ----------------------------------------------------------------
    print("\n--- Step 4: Trying GET endpoints ---")
    get_endpoints = [
        f"/app/map/get_map_list?device_sn={device_id}",
        f"/app/devicemanage/get_device_map?device_sn={device_id}",
        f"/app/clean/get_clean_record_list?device_sn={device_id}",
    ]
    for endpoint in get_endpoints:
        try:
            async with session.get(
                f"{BASE}{endpoint}",
                headers=headers,
            ) as resp:
                status = resp.status
                result = await resp.json()
                code = result.get("code", result.get("res_code", "?"))
                msg = result.get("msg", result.get("message", ""))
                is_valid = (
                    status == 200
                    and code not in (404, "404", -1, 10001)
                    and "not found" not in str(msg).lower()
                    and "no route" not in str(msg).lower()
                )
                if is_valid:
                    print(f"  [HIT] GET {endpoint}")
                    print(f"         status={status} code={code}")
                    safe_name = endpoint.split("?")[0].replace("/", "_").strip("_")
                    (OUTPUT_DIR / f"api_get_{safe_name}.json").write_text(
                        json.dumps(result, indent=2, default=str)
                    )
                    found_endpoints.append(f"GET {endpoint}")
                else:
                    print(f"  [---] GET {endpoint}  (code={code})")
        except Exception as e:
            print(f"  [ERR] GET {endpoint}  ({e})")

    await api.close()

    # Summary
    print(f"\n{'=' * 60}")
    if found_endpoints:
        print(f"Found {len(found_endpoints)} valid endpoint(s):")
        for ep in found_endpoints:
            print(f"  {ep}")
    else:
        print("No map-related API endpoints found.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
