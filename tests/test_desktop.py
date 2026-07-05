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
    gap instead of relying on import order.
    """
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["SARVOS_DB_PATH"] = os.path.join(tmp, "test_desktop.db")
        import api.server
        importlib.reload(api.server)
        import desktop as desktop_module
        importlib.reload(desktop_module)
        yield


import desktop


def test_port_is_open_false_when_nothing_listening():
    assert desktop._port_is_open("127.0.0.1", 65432) is False


def test_server_becomes_reachable_and_serves_real_api():
    """Starts the actual background server thread desktop.py uses, waits
    for _port_is_open to confirm readiness the same way main() does, then
    hits a real endpoint to confirm it's not just a listening socket but
    the actual FastAPI app responding correctly."""
    test_port = 8321
    original_port = desktop.PORT
    desktop.PORT = test_port
    try:
        thread = threading.Thread(target=desktop._run_server, daemon=True)
        thread.start()

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
        desktop.PORT = original_port
