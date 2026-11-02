"""python -m universe_desktop_bridge — serve the desktop bridge.

Binds 127.0.0.1 only (the Electron renderer and dev browser talk to it;
nothing else should). Port: UNIVERSE_BRIDGE_PORT or 8738.
"""
from __future__ import annotations

import os

import uvicorn

from universe_desktop_bridge.app import DEFAULT_PORT


def main() -> int:
    port = int(os.environ.get("UNIVERSE_BRIDGE_PORT", str(DEFAULT_PORT)))
    uvicorn.run("universe_desktop_bridge.app:app", host="127.0.0.1", port=port,
                log_level="info", ws_ping_interval=20.0, ws_ping_timeout=20.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
