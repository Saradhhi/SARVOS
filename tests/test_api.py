"""
API-layer tests. Sets SARVOS_DB_PATH to an isolated temp file BEFORE
importing api.server, since the server module creates its global
Orchestrator/MemoryEngine at import time — importing it with the default
path would pollute (or read stale state from) the real sarvos.db.
"""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_api.db")
        os.environ["SARVOS_DB_PATH"] = db_path

        # Import (or re-import) after the env var is set, and reset the
        # pending-confirmation slot between tests since the module is a
        # long-lived singleton within the test process.
        import importlib
        import api.server as server_module
        importlib.reload(server_module)

        from fastapi.testclient import TestClient
        yield TestClient(server_module.app)


def test_index_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SARVOS" in resp.text


def test_chat_memory_roundtrip(client):
    resp = client.post("/api/chat", json={"message": "remember that I like tea"})
    body = resp.json()
    assert body["status"] == "ok"
    assert any(m["agent"] == "memory" for m in body["messages"])
    assert "I like tea" in body["messages"][0]["output"]


def test_chat_destructive_needs_confirmation(client):
    resp = client.post("/api/chat", json={"message": "delete everything"})
    body = resp.json()
    assert body["status"] == "needs_confirmation"
    assert body["risk"] == "destructive"


def test_confirm_rejected(client):
    client.post("/api/chat", json={"message": "delete everything"})
    resp = client.post("/api/confirm", json={"approved": False})
    body = resp.json()
    assert body["status"] == "ok"
    assert "won't do that" in body["messages"][0]["output"].lower()


def test_confirm_with_no_pending_returns_error(client):
    resp = client.post("/api/confirm", json={"approved": True})
    body = resp.json()
    assert body["status"] == "error"


def test_audit_log_endpoint_reflects_activity(client):
    client.post("/api/chat", json={"message": "remember that I like coffee"})
    resp = client.get("/api/log")
    body = resp.json()
    actions = [e["action"] for e in body["entries"]]
    assert "dispatch" in actions


def test_history_endpoint(client):
    client.post("/api/chat", json={"message": "remember that I like coffee"})
    resp = client.get("/api/history")
    body = resp.json()
    assert any("coffee" in t["content"] for t in body["turns"])
