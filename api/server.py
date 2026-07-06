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

VOICE INTEGRATION: on startup, this also launches the wake-word voice
pipeline (voice/assistant.py's VoiceAssistant) on a background thread,
sharing the SAME _orchestrator as text chat below -- so memory/history
stay unified whether you type or speak. The voice pipeline pushes live
state (wake detected, listening, transcript, thinking, response, speaking)
through a thread-safe queue to an async broadcaster, which fans it out to
any connected browser over WebSocket (/ws/voice-events) -- this is how the
orb UI's visual state stays in sync with what the voice pipeline is
actually doing, in real time.

Gracefully degrades if voice dependencies (openwakeword, faster-whisper,
pyttsx3, sounddevice) aren't installed: the server logs a message and
continues without the voice thread rather than failing to start at all --
text chat and everything else keeps working regardless.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.orchestrator import PendingConfirmation
from core.factory import create_orchestrator

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _broadcast_task, _voice_event_queue, _main_event_loop
    _main_event_loop = asyncio.get_running_loop()
    _voice_event_queue = asyncio.Queue()
    _broadcast_task = asyncio.create_task(_broadcast_loop())
    _start_voice_pipeline()
    yield
    # Shutdown: the broadcast loop runs forever (blocking on the queue) --
    # without explicitly cancelling it, it would keep the asyncio event
    # loop from shutting down cleanly, which was observed directly as a
    # hanging test suite before this cancellation was added.
    #
    # cancel() alone only REQUESTS cancellation -- it doesn't wait for the
    # task to actually finish processing that CancelledException. Without
    # awaiting it afterward, the event loop can shut down while the task
    # is still technically "pending," producing a harmless but noisy
    # "Task was destroyed but it is pending!" warning -- observed
    # consistently across real test runs on Windows. Awaiting it here
    # (swallowing the expected CancelledError) lets it actually finish.
    if _broadcast_task is not None:
        _broadcast_task.cancel()
        try:
            await _broadcast_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="SARVOS", lifespan=_lifespan)

# Overridable via env var so tests can point at an isolated DB instead of
# the real sarvos.db in the working directory.
_db_path = os.environ.get("SARVOS_DB_PATH", "sarvos.db")
_orchestrator = create_orchestrator(_db_path)
_memory = _orchestrator.memory

# Holds the one outstanding confirmation request, if any. A real multi-
# session server would key this by session id instead of a single slot.
_pending: dict = {}

# ---- Voice event bridge -----------------------------------------------
# The voice pipeline runs on a plain OS thread (it does blocking
# microphone I/O), while WebSocket broadcasting lives on the asyncio event
# loop. call_soon_threadsafe is the standard, correct way to bridge a
# background thread's output into an asyncio.Queue -- unlike wrapping a
# blocking queue.Queue.get() in asyncio.to_thread (tried first, and
# reverted): that approach isn't reliably cancellable, since the
# underlying OS thread stays blocked on the real queue.get() call even
# after the asyncio-side task is cancelled, which is exactly what caused
# the test suite to hang on shutdown when this was first built.
_voice_event_queue: asyncio.Queue | None = None
_main_event_loop: asyncio.AbstractEventLoop | None = None
_websocket_clients: list[WebSocket] = []
_broadcast_task: asyncio.Task | None = None

# In-process subscribers: plain Python callbacks (not WebSocket clients)
# notified synchronously whenever a voice event fires. Used by desktop.py
# to react to events directly (e.g. restoring the window when the wake
# word triggers) without needing to be its own WebSocket client just to
# talk to itself within the same process.
_event_subscribers: list = []


def subscribe_to_voice_events(callback) -> None:
    """callback: Callable[[dict], None]. Called from whichever thread
    _emit_voice_event runs on (the voice pipeline's background thread) --
    keep callbacks fast and exception-safe."""
    _event_subscribers.append(callback)


def _emit_voice_event(event: dict) -> None:
    """Called from the voice pipeline's background thread. Safe to call
    from any thread -- call_soon_threadsafe schedules the actual queue put
    onto the main event loop rather than touching asyncio state directly
    from a non-asyncio thread (which is not thread-safe)."""
    for callback in _event_subscribers:
        try:
            callback(event)
        except Exception as e:
            print(f"[server] voice event subscriber raised: {e}")

    if _main_event_loop is not None and _voice_event_queue is not None:
        _main_event_loop.call_soon_threadsafe(_voice_event_queue.put_nowait, event)


async def _broadcast_loop() -> None:
    while True:
        event = await _voice_event_queue.get()
        dead = []
        for ws in _websocket_clients:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in _websocket_clients:
                _websocket_clients.remove(ws)


def _start_voice_pipeline() -> None:
    """Starts the wake-word voice pipeline on a background daemon thread,
    sharing _orchestrator with text chat. Gracefully skipped (server keeps
    running fine without it) if voice dependencies aren't installed, OR if
    SARVOS_DISABLE_VOICE_PIPELINE=1 is set -- used by the test suite so
    tests don't spawn a real background thread trying (and failing) to
    open a microphone that doesn't exist in a CI/test environment."""
    if os.environ.get("SARVOS_DISABLE_VOICE_PIPELINE") == "1":
        return

    try:
        from voice.assistant import VoiceAssistant
    except ImportError as e:
        print(f"[server] Voice pipeline not started (missing dependency: {e}). "
              f"Install with: pip install -r requirements-voice.txt")
        return

    def _run():
        try:
            assistant = VoiceAssistant(_orchestrator, on_event=_emit_voice_event)
            assistant.run()
        except Exception as e:
            # A failure here (e.g. no microphone at all on this machine)
            # must not take down the rest of the server -- text chat and
            # the REST API should keep working regardless.
            print(f"[server] Voice pipeline stopped due to an error: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


@app.websocket("/ws/voice-events")
async def voice_events_ws(websocket: WebSocket):
    await websocket.accept()
    _websocket_clients.append(websocket)
    try:
        while True:
            # We don't expect the client to send anything -- this just
            # blocks until the browser disconnects, so we can clean up.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _websocket_clients:
            _websocket_clients.remove(websocket)


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
