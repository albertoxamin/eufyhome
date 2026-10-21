#!/usr/bin/env python3
"""Device update callbacks from the MQTT thread must run on the event loop.

Run from repo root:
    python scripts/test_notify_thread.py
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT = REPO_ROOT / "custom_components" / "eufy_clean"


def _stub_package(name: str, path: Path) -> None:
    """Register a package without running its __init__ (that imports HA)."""
    import types

    module = types.ModuleType(name)
    module.__path__ = [str(path)]  # type: ignore[attr-defined]
    module.__package__ = name
    sys.modules[name] = module


_stub_package("custom_components", REPO_ROOT / "custom_components")
_stub_package("custom_components.eufy_clean", COMPONENT)
_stub_package("custom_components.eufy_clean.api", COMPONENT / "api")
_stub_package("custom_components.eufy_clean.proto", COMPONENT / "proto")
_stub_package("custom_components.eufy_clean.proto.cloud", COMPONENT / "proto" / "cloud")

from custom_components.eufy_clean.api.controllers import BaseDevice  # noqa: E402


def main() -> None:
    """Fail if an off-loop notify runs the callback on the caller thread."""
    device = BaseDevice({"device_id": "test"})
    loop_ident: int | None = None
    called_from: list[int] = []

    async def scenario() -> None:
        nonlocal loop_ident
        loop_ident = threading.get_ident()

        def callback() -> None:
            called_from.append(threading.get_ident())

        device.add_update_callback(callback)
        device._notify_update()
        await asyncio.to_thread(device._notify_update)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert loop_ident is not None
    assert called_from == [loop_ident, loop_ident], called_from
    print("ok")


if __name__ == "__main__":
    main()
