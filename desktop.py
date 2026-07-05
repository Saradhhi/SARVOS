"""
SARVOS Desktop — native window wrapper around the existing web UI.

This does NOT duplicate any logic: it starts the same FastAPI app
(api/server.py) in a background thread, waits for it to come up, then opens
it in a native OS window via pywebview instead of a browser tab. No address
bar, no tabs, no "why is this in Chrome" — just SARVOS as its own app.

Run:
    python desktop.py

Requires: pip install pywebview  (already in requirements.txt)

This is the pragmatic middle step between "open in a browser" and a full
Electron/Tauri build (which your original spec names as the eventual
target). Nothing here blocks moving to Electron/Tauri later — the backend
and HTML are unchanged either way; only how the window is opened differs.
"""

from __future__ import annotations

import socket
import threading
import time

import uvicorn
import webview

from api.server import app

HOST = "127.0.0.1"
PORT = 8000


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _run_server() -> None:
    # log_level="warning" keeps uvicorn's per-request access logs out of the
    # way, since they'd otherwise clutter the console this window is
    # launched from for no benefit to the user of a desktop app.
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def main() -> None:
    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    # Wait for the server to actually be reachable rather than a fixed
    # sleep — avoids a race where the window opens before uvicorn is ready,
    # which would show a connection-refused error on first launch.
    deadline = time.time() + 15
    while time.time() < deadline:
        if _port_is_open(HOST, PORT):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError(
            f"SARVOS server didn't come up on {HOST}:{PORT} within 15s. "
            "Check for a port conflict (something else using 8000?) or "
            "look for errors above."
        )

    webview.create_window(
        "SARVOS",
        f"http://{HOST}:{PORT}",
        width=1100,
        height=750,
        min_size=(700, 500),
    )
    webview.start()


if __name__ == "__main__":
    main()
