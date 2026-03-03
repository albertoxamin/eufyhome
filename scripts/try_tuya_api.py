#!/usr/bin/env python3
"""
Query the Tuya mobile API for sweeper/map data.

Eufy devices are rebranded Tuya devices. The Tuya mobile API (a1.tuyaeu.com/api.json)
gives direct access to device data using HMAC-signed requests.

Flow:
1. Login to Eufy → get user_center_id
2. Use user_center_id as Tuya uid → authenticate with Tuya mobile API
3. Query device data and sweeper-specific actions

Based on: https://github.com/damacus/robovac/blob/main/custom_components/robovac/tuyawebapi.py

This script is READ-ONLY — it only makes GET requests and queries.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import math
import os
import random
import string
import sys
import time
import types
import uuid
from hashlib import md5, sha256
from pathlib import Path

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "eufy_clean"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUTPUT_DIR = REPO_ROOT / "scripts" / "captured_data"

# Tuya API constants (from damacus/robovac)
EUFY_HMAC_KEY = "A_cepev5pfnhua4dkqkdpmnrdxx378mpjr_s8x78u7xwymasd9kqa7a73pjhxqsedaj".encode()

TUYA_PASSWORD_INNER_KEY = bytearray(
    [36, 78, 109, 138, 86, 172, 135, 145, 36, 67, 45, 139, 108, 188, 162, 196]
)
TUYA_PASSWORD_INNER_IV = bytearray(
    [119, 36, 86, 242, 167, 102, 76, 243, 57, 44, 53, 151, 233, 62, 87, 71]
)

SIGNATURE_RELEVANT_PARAMETERS = {
    "a", "v", "lat", "lon", "lang", "deviceId", "appVersion", "ttid",
    "isH5", "h5Token", "os", "clientId", "postData", "time", "requestId",
    "et", "n4h5", "sid", "sp",
}

DEFAULT_TUYA_QUERY_PARAMS = {
    "appVersion": "2.4.0",
    "deviceId": "",
    "platform": "sdk_gphone64_arm64",
    "clientId": "yx5v9uc3ef9wg3v9atje",
    "lang": "en",
    "osSystem": "12",
    "os": "Android",
    "timeZoneId": "Europe/London",
    "ttid": "android",
    "et": "0.0.1",
    "sdkVersion": "3.0.8cAnker",
}


def shuffled_md5(value: str) -> str:
    _hash = md5(value.encode("utf-8")).hexdigest()
    return _hash[8:16] + _hash[0:8] + _hash[24:32] + _hash[16:24]


def unpadded_rsa(key_exponent: int, key_n: int, plaintext: bytes) -> bytes:
    keylength = math.ceil(key_n.bit_length() / 8)
    input_nr = int.from_bytes(plaintext, byteorder="big")
    crypted_nr = pow(input_nr, key_exponent, key_n)
    return crypted_nr.to_bytes(keylength, byteorder="big")


def get_signature(query_params: dict, encoded_post_data: str) -> str:
    query_params = query_params.copy()
    if encoded_post_data:
        query_params["postData"] = encoded_post_data
    sorted_pairs = sorted(query_params.items())
    filtered_pairs = filter(
        lambda p: p[0] and p[0] in SIGNATURE_RELEVANT_PARAMETERS, sorted_pairs
    )
    mapped_pairs = map(
        lambda p: p[0] + "=" + (shuffled_md5(p[1]) if p[0] == "postData" else p[1]),
        filtered_pairs,
    )
    message = "||".join(mapped_pairs)
    return hmac.HMAC(key=EUFY_HMAC_KEY, msg=message.encode("utf-8"), digestmod=sha256).hexdigest()


def determine_password(username: str) -> str:
    from cryptography.hazmat.backends.openssl import backend as openssl_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(
        algorithms.AES(TUYA_PASSWORD_INNER_KEY),
        modes.CBC(TUYA_PASSWORD_INNER_IV),
        backend=openssl_backend,
    )
    padded_size = 16 * math.ceil(len(username) / 16)
    password_uid = username.zfill(padded_size)
    encryptor = cipher.encryptor()
    encrypted_uid = encryptor.update(password_uid.encode("utf8"))
    encrypted_uid += encryptor.finalize()
    return md5(encrypted_uid.hex().upper().encode("utf-8")).hexdigest()


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


async def tuya_request(
    session: aiohttp.ClientSession,
    base_url: str,
    action: str,
    version: str = "1.0",
    data: dict | None = None,
    query_params_extra: dict | None = None,
    sid: str | None = None,
) -> dict:
    """Make a Tuya mobile API request."""
    device_id = "".join(random.choices(string.ascii_letters + string.digits, k=44))

    qp = DEFAULT_TUYA_QUERY_PARAMS.copy()
    qp["deviceId"] = device_id
    if sid:
        qp["sid"] = sid

    current_time = str(int(time.time()))
    request_id = str(uuid.uuid4())
    qp.update({
        "time": current_time,
        "requestId": request_id,
        "a": action,
        "v": version,
        **(query_params_extra or {}),
    })

    encoded_post_data = json.dumps(data, separators=(",", ":")) if data else ""
    qp["sign"] = get_signature(qp, encoded_post_data)

    post_data = {"postData": encoded_post_data} if encoded_post_data else None

    async with session.post(
        base_url + "/api.json",
        params=qp,
        data=post_data,
        headers={"User-Agent": "TY-UA=APP/Android/2.4.0/SDK/null"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        result = await resp.json()
        return result


async def main() -> None:
    username, password = load_credentials()

    # Step 1: Login to Eufy to get user_center_id
    print("=" * 60)
    print("Tuya Mobile API Explorer - READ ONLY")
    print("=" * 60)

    session = aiohttp.ClientSession()

    # Eufy login
    print("\n  Step 1: Eufy login...")
    eufy_headers = {
        "category": "Home",
        "Accept": "*/*",
        "openudid": "sdk_gphone64_arm64",
        "Accept-Language": "en-US;q=1",
        "Content-Type": "application/json",
        "clientType": "1",
        "language": "en",
        "User-Agent": "EufyHome-iOS-2.14.0-6",
        "timezone": "Europe/London",
        "country": "US",
    }
    async with session.post(
        "https://home-api.eufylife.com/v1/user/email/login",
        headers=eufy_headers,
        json={
            "email": username,
            "password": password,
            "client_id": "eufyhome-app",
            "client_secret": "GQCpr9dSp3uQpsOMgJ4xQ",
        },
    ) as resp:
        eufy_login = await resp.json()

    access_token = eufy_login.get("access_token", "")
    eufy_user_id = eufy_login.get("user_id", "")
    print(f"  Eufy user_id: {eufy_user_id[:20]}...")

    # Get user_center_id
    print("  Getting user_center_id...")
    eufy_headers2 = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": "EufyHome-Android-3.1.3-753",
        "timezone": "Europe/London",
        "category": "Home",
        "token": access_token,
        "openudid": "sdk_gphone64_arm64",
        "clienttype": "2",
        "language": "en",
        "country": "US",
    }
    async with session.get(
        "https://api.eufylife.com/v1/user/user_center_info",
        headers=eufy_headers2,
    ) as resp:
        user_center = await resp.json()

    user_center_id = user_center.get("user_center_id", "")
    print(f"  user_center_id: {user_center_id[:20]}...")

    # Get device list from Eufy to find device SN
    print("  Getting Eufy devices...")
    async with session.get(
        "https://api.eufylife.com/v1/device/v2",
        headers=eufy_headers2,
    ) as resp:
        eufy_devices = await resp.json()

    devices = eufy_devices.get("data", eufy_devices).get("devices", [])
    print(f"  Found {len(devices)} Eufy device(s)")

    device_id = ""
    for d in devices:
        did = d.get("id", "")
        dname = d.get("alias_name", d.get("device_name", ""))
        product = d.get("product", {})
        model = product.get("product_code", "")
        print(f"    - {dname} ({model}): {did}")
        if not device_id:
            device_id = did

    # Also get user settings for Tuya region info
    print("  Getting Eufy user settings...")
    async with session.get(
        "https://api.eufylife.com/v1/user/setting",
        headers=eufy_headers2,
    ) as resp:
        user_settings = await resp.json()

    # Determine Tuya region from settings
    tuya_region = "EU"
    phone_code = "44"
    home_setting = user_settings.get("setting", {}).get("home_setting", {})
    tuya_home = home_setting.get("tuya_home", {})
    if "tuya_region_code" in tuya_home:
        tuya_region = tuya_home["tuya_region_code"]
    print(f"  Tuya region: {tuya_region}")
    print(f"  Tuya home settings: {json.dumps(tuya_home, indent=2)[:300]}")

    # Step 2: Tuya mobile API login
    # KEY: The Tuya uid is "eh-" + eufy_user_id (NOT user_center_id)
    # Source: damacus/robovac config_flow.py line 152
    tuya_uid = "eh-" + eufy_user_id
    print(f"\n  Step 2: Tuya mobile API login (uid={tuya_uid[:20]}...)")

    base_url = {
        "AZ": "https://a1.tuyaus.com",
        "AY": "https://a1.tuyacn.com",
        "IN": "https://a1.tuyain.com",
        "EU": "https://a1.tuyaeu.com",
    }.get(tuya_region, "https://a1.tuyaeu.com")

    # Get token
    print(f"  Requesting token from {base_url}...")
    token_resp = await tuya_request(
        session, base_url,
        action="tuya.m.user.uid.token.create",
        data={"uid": tuya_uid, "countryCode": phone_code},
    )
    print(f"  Token response: {json.dumps(token_resp, indent=2)[:500]}")

    if "result" not in token_resp:
        print(f"  ERROR: No result in token response")
        await session.close()
        return

    token_result = token_resp["result"]

    # Login with password
    tuya_password = determine_password(tuya_uid)
    encrypted_password = unpadded_rsa(
        key_exponent=int(token_result["exponent"]),
        key_n=int(token_result["publicKey"]),
        plaintext=tuya_password.encode("utf-8"),
    )

    print("  Logging in...")
    login_resp = await tuya_request(
        session, base_url,
        action="tuya.m.user.uid.password.login.reg",
        data={
            "uid": tuya_uid,
            "createGroup": True,
            "ifencrypt": 1,
            "passwd": encrypted_password.hex(),
            "countryCode": phone_code,
            "options": '{"group": 1}',
            "token": token_result["token"],
        },
    )
    print(f"  Login response keys: {list(login_resp.get('result', {}).keys()) if 'result' in login_resp else 'ERROR'}")

    if "result" not in login_resp:
        print(f"  Login failed: {json.dumps(login_resp, indent=2)[:500]}")
        # Try with md5("12345678") as fallback
        print("  Trying fallback password...")
        fallback_pw = md5("12345678".encode("utf8")).hexdigest()
        encrypted_fallback = unpadded_rsa(
            key_exponent=int(token_result["exponent"]),
            key_n=int(token_result["publicKey"]),
            plaintext=fallback_pw.encode("utf-8"),
        )
        login_resp = await tuya_request(
            session, base_url,
            action="tuya.m.user.uid.password.login.reg",
            data={
                "uid": tuya_uid,
                "createGroup": True,
                "ifencrypt": 1,
                "passwd": encrypted_fallback.hex(),
                "countryCode": phone_code,
                "options": '{"group": 1}',
                "token": token_result["token"],
            },
        )
        if "result" not in login_resp:
            print(f"  Fallback also failed: {json.dumps(login_resp, indent=2)[:500]}")
            await session.close()
            return

    login_result = login_resp["result"]
    sid = login_result.get("sid", "")
    tuya_base_url = login_result.get("domain", {}).get("mobileApiUrl", base_url)
    print(f"  Session ID: {sid[:20]}...")
    print(f"  API base URL: {tuya_base_url}")

    # Step 3: Query device and sweeper data
    print(f"\n  Step 3: Querying device and sweeper data...")

    # List of Tuya actions to try
    actions = [
        {
            "name": "Get device info",
            "action": "tuya.m.device.get",
            "version": "1.0",
            "data": {"devId": device_id},
        },
        {
            "name": "Get device DP",
            "action": "tuya.m.device.dp.get",
            "version": "2.0",
            "data": {"devId": device_id},
        },
        {
            "name": "List homes",
            "action": "tuya.m.location.list",
            "version": "2.1",
            "data": None,
        },
        {
            "name": "Get sweeper map (media latest)",
            "action": "tuya.m.device.media.latest",
            "version": "2.0",
            "data": {"devId": device_id, "start": "", "size": 500},
        },
        {
            "name": "Get sweeper media detail",
            "action": "tuya.m.device.media.detail",
            "version": "2.0",
            "data": {"devId": device_id, "subRecordId": "0"},
        },
        {
            "name": "Get sweeper media list",
            "action": "tuya.m.sweeper.media.list",
            "version": "1.0",
            "data": {"devId": device_id},
        },
        {
            "name": "Get device info (group list v1, login gid)",
            "action": "tuya.m.my.group.device.list",
            "version": "1.0",
            "data": {"gid": str(login_result.get("gid", ""))},
        },
        {
            "name": "Get device info (group list v1, home gid)",
            "action": "tuya.m.my.group.device.list",
            "version": "1.0",
            "data": {"gid": "68675423"},
        },
        {
            "name": "Get device (T2118 700203248caab5f05019)",
            "action": "tuya.m.device.get",
            "version": "1.0",
            "data": {"devId": "700203248caab5f05019"},
        },
        {
            "name": "Get device DP report",
            "action": "tuya.m.device.dp.report",
            "version": "1.0",
            "data": {"devId": device_id},
        },
        {
            "name": "Get sweeper file",
            "action": "tuya.m.device.media.file.get",
            "version": "1.0",
            "data": {"devId": device_id},
        },
        {
            "name": "Get clean records",
            "action": "tuya.m.device.media.history",
            "version": "1.0",
            "data": {"devId": device_id, "start": 0, "size": 10},
        },
    ]

    results = {}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for act in actions:
        name = act["name"]
        print(f"\n  {'─' * 55}")
        print(f"    {name}")
        print(f"    Action: {act['action']} v{act['version']}")
        print(f"  {'─' * 55}")

        try:
            resp = await tuya_request(
                session, tuya_base_url,
                action=act["action"],
                version=act["version"],
                data=act["data"],
                sid=sid,
            )

            body_str = json.dumps(resp, indent=2, default=str)
            if len(body_str) > 3000:
                print(f"    Response ({len(body_str)} chars, truncated):")
                print(f"    {body_str[:3000]}...")
            else:
                print(f"    Response:")
                print(f"    {body_str}")

            results[name] = resp

        except Exception as e:
            print(f"    Error: {e}")
            results[name] = {"error": str(e)}

    await session.close()

    # Save results
    (OUTPUT_DIR / "tuya_api_results.json").write_text(
        json.dumps(results, indent=2, default=str)
    )
    print(f"\n{'=' * 60}")
    print(f"All results saved to {OUTPUT_DIR / 'tuya_api_results.json'}")

    # Summary
    print(f"\n{'=' * 60}")
    print("Summary:")
    print(f"{'=' * 60}")
    for name, r in results.items():
        success = "result" in r if isinstance(r, dict) else False
        error_code = r.get("errorCode", "") if isinstance(r, dict) else ""
        error_msg = r.get("errorMsg", r.get("error", "")) if isinstance(r, dict) else ""
        status = "OK" if success else f"FAIL ({error_code}: {error_msg})"
        print(f"  {status:>40}  {name}")


if __name__ == "__main__":
    asyncio.run(main())
