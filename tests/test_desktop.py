"""
desktop.py's window-opening (webview.start()) can't be tested here — it
needs a real display/GUI backend (WebView2 on Windows, GTK/Qt on Linux),
which this environment doesn't have. What CAN be verified without a display
is the part most likely to silently break: does the background server
thread actually come up, and is _port_is_open's readiness check accurate?
That's the logic that decides whether the desktop window opens onto a
working server or a connection-refused error.
"""

from __future__ import annotations

import importlib
import os
import tempfile
import threading
import time

import pytest
import requests


@pytest.fixture(autouse=True)
def isolated_db():
    """
    api.server holds its Orchestrator/MemoryEngine as module-level globals,
    created once at import time from SARVOS_DB_PATH. Because Python caches
    imported modules, just setting the env var here does nothing if
    api.server (and desktop, which does `from api.server import app`) were
    already imported with a different path — e.g. by test_api.py running
    first in the same test session, pointed at a temp dir that no longer
    exists by the time this test runs. That exact scenario is what caused
    "unable to open database file" the first time this test suite ran as a
    whole (each file passed alone, only the combination failed).

    Reloading both modules after setting the env var forces a fresh
    MemoryEngine/Orchestrator bound to THIS test's temp path, closing that
    gap instead of relying on import order. Also disables the voice
    pipeline -- not needed for these tests, and avoids unnecessary
    background-thread noise.
    """
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["SARVOS_DB_PATH"] = os.path.join(tmp, "test_desktop.db")
        os.environ["SARVOS_DISABLE_VOICE_PIPELINE"] = "1"
        import api.server
        importlib.reload(api.server)
        import desktop as desktop_module
        importlib.reload(desktop_module)
        yield


import desktop


def test_port_is_open_false_when_nothing_listening():
    assert desktop._port_is_open("127.0.0.1", 65432) is False


def test_server_becomes_reachable_and_serves_real_api():
    """Starts a REAL, controllable uvicorn.Server (desktop._build_server())
    the same way desktop.py's main() does, waits for _port_is_open to
    confirm readiness, hits a real endpoint to confirm it's not just a
    listening socket but the actual FastAPI app responding correctly --
    and then shuts the server down CLEANLY via should_exit, so FastAPI's
    lifespan shutdown actually fires (properly cancelling the broadcast
    task) instead of abandoning the server on a daemon thread forever.

    That abandonment was the real, if cosmetic, source of a "Task was
    destroyed but it is pending!" warning at the end of the whole test
    session -- fixed by giving this test a way to shut down what it
    started, not just leave it running."""
    test_port = 8321
    server = desktop._build_server(host=desktop.HOST, port=test_port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if desktop._port_is_open(desktop.HOST, test_port):
                break
            time.sleep(0.1)
        else:
            pytest.fail("Server did not become reachable within 10s")

        resp = requests.post(
            f"http://{desktop.HOST}:{test_port}/api/chat",
            json={"message": "remember that I like the desktop app"},
            timeout=5,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
