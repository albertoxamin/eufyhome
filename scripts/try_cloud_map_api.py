#!/usr/bin/env python3
"""
Try various cloud API endpoints for map/path data.

Tests both:
1. Eufy's own API (aiot-clean-api-pr.eufylife.com)
2. Tuya standard sweeper API (/v1.0/users/sweepers/file/{device_id}/realtime-map)

This script is READ-ONLY — it only makes GET requests.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import sys
import time
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
    import types
    import aiohttp

    username, password = load_credentials()

    sys.modules["eufy_clean"] = types.ModuleType("eufy_clean")
    sys.modules["eufy_clean.api"] = types.ModuleType("eufy_clean.api")
    _load_module("eufy_clean.const", COMPONENT_ROOT / "const.py")
    _load_module("eufy_clean.api.proto_utils", COMPONENT_ROOT / "api" / "proto_utils.py")
    _load_module("eufy_clean.api.eufy_api", COMPONENT_ROOT / "api" / "eufy_api.py")
    from eufy_clean.api.eufy_api import EufyCleanApi

    print("=" * 60)
    print("Cloud Map API Explorer - READ ONLY")
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
    print(f"Device SN: {device_id}")

    # Get auth tokens
    user_info = api.user_info
    mqtt_creds = api.mqtt_credentials
    user_center_token = user_info.get("user_center_token", "") if user_info else ""
    gtoken = user_info.get("gtoken", "") if user_info else ""
    access_token = api._access_token or ""
    openudid = api.openudid

    # Common headers for Eufy API
    eufy_headers = {
        "user-agent": "EufyHome-Android-3.1.3-753",
        "timezone": "Europe/Berlin",
        "openudid": openudid,
        "language": "en",
        "country": "US",
        "os-version": "Android",
        "model-type": "PHONE",
        "app-name": "eufy_home",
        "x-auth-token": user_center_token,
        "gtoken": gtoken,
        "content-type": "application/json; charset=UTF-8",
    }

    # Common headers for Eufy Cloud (eufylife.com)
    eufy_cloud_headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": "EufyHome-Android-3.1.3-753",
        "timezone": "Europe/Berlin",
        "category": "Home",
        "token": access_token,
        "openudid": openudid,
        "clienttype": "2",
        "language": "en",
        "country": "US",
    }

    session = aiohttp.ClientSession()

    # List of endpoints to try
    endpoints = [
        # Eufy MQTT API - map related
        {
            "name": "Eufy: get_map_data",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/devicemanage/get_map_data",
            "headers": eufy_headers,
            "json": {"device_sn": device_id},
        },
        {
            "name": "Eufy: get_map_list",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/devicemanage/get_map_list",
            "headers": eufy_headers,
            "json": {"device_sn": device_id},
        },
        {
            "name": "Eufy: get_multi_map",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/devicemanage/get_multi_map",
            "headers": eufy_headers,
            "json": {"device_sn": device_id},
        },
        {
            "name": "Eufy: get_clean_record",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/devicemanage/get_clean_record",
            "headers": eufy_headers,
            "json": {"device_sn": device_id},
        },
        {
            "name": "Eufy: get_clean_history",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/devicemanage/get_clean_history",
            "headers": eufy_headers,
            "json": {"device_sn": device_id},
        },
        {
            "name": "Eufy: device_info",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/devicemanage/device_info",
            "headers": eufy_headers,
            "json": {"device_sn": device_id},
        },
        # Tuya standard sweeper API (may not work with Eufy auth)
        {
            "name": "Tuya: realtime-map",
            "method": "GET",
            "url": f"https://aiot-clean-api-pr.eufylife.com/v1.0/users/sweepers/file/{device_id}/realtime-map",
            "headers": eufy_headers,
        },
        {
            "name": "Tuya: sweeper-map via MQTT API",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/thing/get_sweeper_map",
            "headers": eufy_headers,
            "json": {"device_sn": device_id},
        },
        {
            "name": "Eufy: get_device_data",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/devicerelation/get_device_data",
            "headers": eufy_headers,
            "json": {"device_sn": device_id, "attribute": 3},
        },
        # Try fetching with specific DPS keys
        {
            "name": "Eufy: get_dps_value (171)",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/devicemanage/get_dps_value",
            "headers": eufy_headers,
            "json": {"device_sn": device_id, "dps_keys": ["171"]},
        },
        {
            "name": "Eufy: get_dps_value (166)",
            "method": "POST",
            "url": "https://aiot-clean-api-pr.eufylife.com/app/devicemanage/get_dps_value",
            "headers": eufy_headers,
            "json": {"device_sn": device_id, "dps_keys": ["166"]},
        },
        # EufyLife cloud API endpoints
        {
            "name": "Eufy Cloud: device detail",
            "method": "GET",
            "url": f"https://api.eufylife.com/v1/device/{device_id}",
            "headers": eufy_cloud_headers,
        },
    ]

    results = {}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for ep in endpoints:
        name = ep["name"]
        print(f"\n{'─' * 60}")
        print(f"  {name}")
        print(f"  {ep['method']} {ep['url']}")
        print(f"{'─' * 60}")

        try:
            if ep["method"] == "POST":
                async with session.post(
                    ep["url"],
                    headers=ep["headers"],
                    json=ep.get("json", {}),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    status = resp.status
                    try:
                        body = await resp.json()
                    except Exception:
                        body = await resp.text()
            else:
                async with session.get(
                    ep["url"],
                    headers=ep["headers"],
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    status = resp.status
                    try:
                        body = await resp.json()
                    except Exception:
                        body = await resp.text()

            print(f"  Status: {status}")
            body_str = json.dumps(body, indent=2, default=str) if isinstance(body, (dict, list)) else str(body)
            # Truncate long responses
            if len(body_str) > 2000:
                print(f"  Response ({len(body_str)} chars, truncated):")
                print(f"  {body_str[:2000]}...")
            else:
                print(f"  Response:")
                print(f"  {body_str}")

            results[name] = {"status": status, "body": body}

        except Exception as e:
            print(f"  Error: {e}")
            results[name] = {"status": "error", "error": str(e)}

    await session.close()
    await api.close()

    # Save all results
    (OUTPUT_DIR / "cloud_map_api_results.json").write_text(
        json.dumps(results, indent=2, default=str)
    )
    print(f"\n{'=' * 60}")
    print(f"All results saved to {OUTPUT_DIR / 'cloud_map_api_results.json'}")

    # Summary
    print(f"\n{'=' * 60}")
    print("Summary:")
    print(f"{'=' * 60}")
    for name, r in results.items():
        status = r.get("status", "?")
        body = r.get("body", {})
        code = ""
        if isinstance(body, dict):
            code = body.get("code", body.get("res_code", ""))
            msg = body.get("msg", body.get("message", ""))
            if code != "":
                code = f" (code={code}, msg={msg})"
        print(f"  {status:>5}  {name}{code}")


if __name__ == "__main__":
    asyncio.run(main())
