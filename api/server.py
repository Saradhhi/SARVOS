"""
SARVOS local web server.

This is purely a transport layer on top of the existing Orchestrator/
MemoryEngine/Agents — none of that logic changes. Run this instead of
main.py's CLI loop when you want the web UI:

    uvicorn api.server:app --reload

Then open http://localhost:8000

Single-user, local-only design: one global Orchestrator instance, one
pending-confirmation slot at a time (matches how the CLI already works —
you can't have two unresolved confirmations in flight, since the CLI blocks
on the answer). This is NOT designed for multiple simultaneous users; that
would need per-session orchestrator state, which is a real but separable
future change.
"""

from __future__ import annotations

import os
import uuid

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.orchestrator import Orchestrator, PendingConfirmation
from core.schemas import AgentName
from memory.engine import MemoryEngine
from memory.store import Store
from agents.planner import PlannerAgent
from agents.coding import CodingAgent
from agents.memory_agent import MemoryAgent
from agents.general import GeneralAgent

app = FastAPI(title="SARVOS")

# Overridable via env var so tests can point at an isolated DB instead of
# the real sarvos.db in the working directory.
_db_path = os.environ.get("SARVOS_DB_PATH", "sarvos.db")
_memory = MemoryEngine(store=Store(_db_path))
_agents = {
    AgentName.PLANNER: PlannerAgent(_memory),
    AgentName.CODING: CodingAgent(_memory),
    AgentName.MEMORY: MemoryAgent(_memory),
    AgentName.GENERAL: GeneralAgent(_memory),
}
_orchestrator = Orchestrator(_memory, _agents)

# Holds the one outstanding confirmation request, if any. A real multi-
# session server would key this by session id instead of a single slot.
_pending: dict = {}


class ChatRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    approved: bool


def _serialize_results(results) -> list[dict]:
    return [
        {
            "agent": r.agent.value,
            "success": r.success,
            "output": r.output,
        }
        for r in results
        if r.output and not r.new_tasks
    ]


@app.post("/api/chat")
def chat(req: ChatRequest):
    request_id = str(uuid.uuid4())
    try:
        results = _orchestrator.handle_user_message(req.message, request_id)
        return {"status": "ok", "messages": _serialize_results(results)}
    except PendingConfirmation as pending:
        _pending["task"] = pending.task
        _pending["request_id"] = request_id
        return {
            "status": "needs_confirmation",
            "prompt": pending.prompt,
            "risk": pending.task.risk.value,
        }


@app.post("/api/confirm")
def confirm(req: ConfirmRequest):
    if "task" not in _pending:
        return {"status": "error", "detail": "No pending confirmation."}
    task = _pending.pop("task")
    request_id = _pending.pop("request_id")
    try:
        results = _orchestrator.resume_with_confirmation(task, req.approved, request_id)
        return {"status": "ok", "messages": _serialize_results(results)}
    except PendingConfirmation as pending:
        # A follow-up task also turned out to need confirmation.
        _pending["task"] = pending.task
        _pending["request_id"] = request_id
        return {
            "status": "needs_confirmation",
            "prompt": pending.prompt,
            "risk": pending.task.risk.value,
        }


@app.get("/api/history")
def history(limit: int = 50):
    turns = _memory.recent_history(limit=limit)
    return {
        "turns": [
            {
                "role": t.role,
                "content": t.content,
                "agent": t.agent.value if t.agent else None,
                "timestamp": t.timestamp.isoformat(),
            }
            for t in turns
        ]
    }


@app.get("/api/log")
def audit_log(limit: int = 30):
    return {"entries": _memory.store.recent_audit_log(limit=limit)}


import pathlib

_STATIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "static"

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(_STATIC_DIR / "index.html"))
