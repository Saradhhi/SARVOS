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

BACKGROUND-READY BEHAVIOR: the window starts minimized (out of your way,
but still running and listening for the wake word), and automatically
restores and comes to the front the moment "Hey Jarvis" triggers -- so you
don't need to have manually opened/focused the app first. Combine with a
Windows Startup shortcut (see README's "Auto-start at login" section) so
SARVOS is always ready without you having to remember to launch it.
"""

from __future__ import annotations

import os
import socket
import threading
import time

import uvicorn
import webview

from api.server import app, subscribe_to_voice_events

HOST = "127.0.0.1"
PORT = 8000
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "sarvos_icon.ico")


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _build_server(host: str = HOST, port: int = PORT) -> uvicorn.Server:
    """Returns a controllable uvicorn.Server instance rather than just
    calling uvicorn.run() directly. The real app (main(), below) still
    just runs it and lets the process exit take it down naturally -- but
    exposing the Server object lets tests shut it down cleanly via
    server.should_exit = True, which properly triggers FastAPI's lifespan
    shutdown (and therefore the broadcast task's clean cancellation) --
    something a bare uvicorn.run() call in a daemon thread has no way to
    do. Without this, tests that started a real server had no way to stop
    it, leaving an abandoned pending asyncio task that produced a
    harmless but noisy "Task was destroyed but it is pending!" warning at
    the end of the whole test session."""
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    return uvicorn.Server(config)


def _run_server() -> None:
    # log_level="warning" keeps uvicorn's per-request access logs out of the
    # way, since they'd otherwise clutter the console this window is
    # launched from for no benefit to the user of a desktop app.
    _build_server().run()


def _minimize_on_startup(window) -> None:
    """Runs shortly after the window is created (via webview.start's func
    callback) -- starts minimized so SARVOS sits quietly in the background
    until woken up, rather than always occupying screen space."""
    time.sleep(0.5)  # let the window fully render once before minimizing
    try:
        window.minimize()
    except Exception as e:
        print(f"[desktop] Could not minimize on startup: {e}")


def _restore_on_wake_word(window):
    """Returns a callback for subscribe_to_voice_events: brings the window
    to the front the moment 'Hey Jarvis' is detected, so you don't need to
    have already had the app open/focused."""
    def _on_event(event: dict) -> None:
        if event.get("type") == "wake_detected":
            try:
                window.restore()
                window.show()
            except Exception as e:
                print(f"[desktop] Could not restore window on wake word: {e}")
    return _on_event


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

    window = webview.create_window(
        "SARVOS",
        f"http://{HOST}:{PORT}",
        width=1100,
        height=750,
        min_size=(700, 500),
    )

    subscribe_to_voice_events(_restore_on_wake_word(window))

    icon = ICON_PATH if os.path.exists(ICON_PATH) else None
    if icon is None:
        print(f"[desktop] Icon not found at {ICON_PATH}, using default.")
    webview.start(func=_minimize_on_startup, args=(window,), icon=icon)


if __name__ == "__main__":
    main()
