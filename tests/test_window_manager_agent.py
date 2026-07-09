"""
Tests for WindowManagerAgent against a FAKE backend.

This agent could not be executed at all in the environment it was written
in: `pygetwindow` raises NotImplementedError on IMPORT under Linux. So all
real Windows calls live behind WindowBackend, a seam with no logic in it,
and everything with behavior worth testing is tested against a substitute.
What is genuinely verified here: routing, risk tiers, title matching,
ambiguity refusal, error paths, and the real confirmation gate on close.
What is NOT verified anywhere: that pygetwindow's own methods do what their
names say on real Windows. That awaits real hardware, and is stated plainly
rather than papered over.
"""

import tempfile
from pathlib import Path

import pytest

from agents.window_manager import WindowBackend, WindowManagerAgent, WindowUnavailable
from core.factory import create_orchestrator
from core.orchestrator import PendingConfirmation
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str) -> Task:
    return Task(parent_request_id="r1", agent=AgentName.WINDOW_MANAGER, instruction=instruction)


class FakeWindow:
    def __init__(self, title):
        self.title = title
        self.calls = []
        self.pos = None
        self.size = None

    def activate(self): self.calls.append("activate")
    def minimize(self): self.calls.append("minimize")
    def maximize(self): self.calls.append("maximize")
    def restore(self): self.calls.append("restore")
    def close(self): self.calls.append("close")
    def moveTo(self, x, y): self.calls.append("move"); self.pos = (x, y)
    def resizeTo(self, w, h): self.calls.append("resize"); self.size = (w, h)


class FakeBackend(WindowBackend):
    def __init__(self, windows=None, active=None):
        self.windows = windows if windows is not None else []
        self._active = active

    def all_windows(self): return list(self.windows)
    def active_window(self): return self._active


def _agent(backend):
    tmp = tempfile.mkdtemp()
    memory = MemoryEngine(store=Store(Path(tmp) / "w.db"))
    return WindowManagerAgent(memory, backend=backend)


# ---- SAFE ---------------------------------------------------------------

def test_list_windows():
    a = _agent(FakeBackend([FakeWindow("Notepad"), FakeWindow("Chrome")]))
    r = a.handle(_task("list windows"))
    assert r.success
    assert r.data["windows"] == ["Notepad", "Chrome"]
    assert "Notepad" in r.output


def test_list_windows_when_none_open():
    r = _agent(FakeBackend([])).handle(_task("list windows"))
    assert r.success
    assert r.data["windows"] == []


def test_active_window():
    win = FakeWindow("Notepad")
    r = _agent(FakeBackend([win], active=win)).handle(_task("what's the active window"))
    assert r.success
    assert r.data["active"] == "Notepad"


def test_active_window_when_none_focused():
    r = _agent(FakeBackend([], active=None)).handle(_task("what's the active window"))
    assert r.success
    assert r.data["active"] is None


# ---- SENSITIVE ----------------------------------------------------------

def test_minimize_by_title():
    win = FakeWindow("Untitled - Notepad")
    a = _agent(FakeBackend([win]))
    r = a.handle(_task("minimize notepad"))
    assert r.success
    assert win.calls == ["minimize"]


def test_minimize_bare_targets_the_active_window():
    active = FakeWindow("Chrome")
    other = FakeWindow("Notepad")
    a = _agent(FakeBackend([active, other], active=active))
    r = a.handle(_task("minimize"))
    assert r.success
    assert active.calls == ["minimize"]
    assert other.calls == []


def test_focus_and_maximize_and_restore():
    win = FakeWindow("Notepad")
    a = _agent(FakeBackend([win]))
    a.handle(_task("focus notepad"))
    a.handle(_task("maximize notepad"))
    a.handle(_task("restore notepad"))
    assert win.calls == ["activate", "maximize", "restore"]


def test_move_passes_real_coordinates():
    win = FakeWindow("Notepad")
    a = _agent(FakeBackend([win]))
    r = a.handle(_task("move notepad to 100, 200"))
    assert r.success
    assert win.pos == (100, 200)


def test_resize_passes_real_dimensions():
    win = FakeWindow("Notepad")
    a = _agent(FakeBackend([win]))
    r = a.handle(_task("resize notepad to 800x600"))
    assert r.success
    assert win.size == (800, 600)


# ---- Matching and refusal ------------------------------------------------

def test_no_match_reports_clearly():
    r = _agent(FakeBackend([FakeWindow("Chrome")])).handle(_task("minimize notepad"))
    assert not r.success
    assert r.error == "window_not_resolved"
    assert "notepad" in r.output.lower()


def test_ambiguous_match_refuses_rather_than_guessing():
    """Silently minimizing the wrong window is a bad surprise. Silently
    CLOSING the wrong one could lose real work. Refuse instead."""
    a = _agent(FakeBackend([FakeWindow("doc1 - Word"), FakeWindow("doc2 - Word")]))
    r = a.handle(_task("close the word window"))
    assert not r.success
    assert "matches 2 windows" in r.output
    assert "won't guess" in r.output.lower()


def test_matching_is_case_insensitive_substring():
    win = FakeWindow("Untitled - NOTEPAD")
    a = _agent(FakeBackend([win]))
    assert a.handle(_task("minimize notepad")).success


def test_no_active_window_reports_clearly():
    r = _agent(FakeBackend([], active=None)).handle(_task("minimize"))
    assert not r.success
    assert "no active window" in r.output.lower()


def test_backend_failure_is_reported_not_raised():
    class Broken(FakeBackend):
        def minimize(self, win): raise RuntimeError("access denied")
    win = FakeWindow("Notepad")
    r = _agent(Broken([win])).handle(_task("minimize notepad"))
    assert not r.success
    assert r.error == "window_minimize_failed"
    assert "access denied" in r.output


def test_window_unavailable_degrades_gracefully():
    """The real failure on any non-Windows machine."""
    class Unavailable(FakeBackend):
        def all_windows(self): raise WindowUnavailable("needs Windows")
    r = _agent(Unavailable()).handle(_task("list windows"))
    assert not r.success
    assert r.error == "window_unavailable"


def test_unrecognized_instruction_gives_helpful_message():
    r = _agent(FakeBackend([])).handle(_task("do something windowy but vague"))
    assert not r.success
    assert "list windows" in r.output


# ---- The real module must import on a non-Windows machine ---------------

def test_module_imports_and_backend_degrades_on_this_platform():
    """pygetwindow raises NotImplementedError on IMPORT under Linux. If the
    import weren't lazy, this module -- and the whole agent registry that
    imports it -- would fail to load at all."""
    import sys
    backend = WindowBackend()
    if sys.platform.startswith("win"):
        pytest.skip("This asserts the non-Windows degradation path.")
    with pytest.raises(WindowUnavailable):
        backend.all_windows()


# ---- DESTRUCTIVE: close is gated by the REAL orchestrator ---------------

def test_close_is_gated_and_only_runs_after_approval(tmp_path, monkeypatch):
    win = FakeWindow("Untitled - Notepad")
    backend = FakeBackend([win])

    orchestrator = create_orchestrator(str(tmp_path / "wm.db"))
    orchestrator.agents[AgentName.WINDOW_MANAGER].backend = backend

    try:
        orchestrator.handle_user_message("close the notepad window", request_id="r1")
        pytest.fail("close must be gated")
    except PendingConfirmation as e:
        pending = e

    assert win.calls == [], "window closed BEFORE confirmation -- must never happen"

    results = orchestrator.resume_with_confirmation(pending.task, approved=True, request_id="r1")
    assert results[-1].success
    assert win.calls == ["close"]


def test_close_rejected_never_closes(tmp_path):
    win = FakeWindow("Untitled - Notepad")
    orchestrator = create_orchestrator(str(tmp_path / "wm2.db"))
    orchestrator.agents[AgentName.WINDOW_MANAGER].backend = FakeBackend([win])

    try:
        orchestrator.handle_user_message("close the notepad window", request_id="r1")
        pytest.fail("expected gate")
    except PendingConfirmation as e:
        pending = e

    results = orchestrator.resume_with_confirmation(pending.task, approved=False, request_id="r1")
    assert not results[-1].success
    assert win.calls == []


def test_minimize_is_not_gated():
    """SENSITIVE, not DESTRUCTIVE -- trivially reversible, so no prompt."""
    import tempfile as tf
    win = FakeWindow("Notepad")
    orchestrator = create_orchestrator(str(Path(tf.mkdtemp()) / "wm3.db"))
    orchestrator.agents[AgentName.WINDOW_MANAGER].backend = FakeBackend([win])
    results = orchestrator.handle_user_message("minimize notepad", request_id="r1")
    assert results[-1].success
    assert win.calls == ["minimize"]


# ---- preflight: don't ask to confirm destroying something nonexistent ---

def test_close_nonexistent_window_never_prompts(tmp_path):
    """Real annoyance found in live testing: 'close the notepad window' with
    no Notepad open prompted 'This looks destructive. Proceed? [y/n]', waited
    for a 'y', and only then said 'No open window matching notepad'. The
    person was asked to authorize destroying something that didn't exist.

    The read-only preflight now resolves the target before the gate."""
    orchestrator = create_orchestrator(str(tmp_path / "pf.db"))
    orchestrator.agents[AgentName.WINDOW_MANAGER].backend = FakeBackend([FakeWindow("Chrome")])

    results = orchestrator.handle_user_message("close the notepad window", request_id="r1")
    assert not results[-1].success
    assert results[-1].error == "window_not_resolved"


def test_preflight_does_not_weaken_the_gate(tmp_path):
    """The preflight may only REFUSE, never act. A window that DOES exist
    must still be gated, and must not be closed before approval."""
    win = FakeWindow("Untitled - Notepad")
    orchestrator = create_orchestrator(str(tmp_path / "pf2.db"))
    orchestrator.agents[AgentName.WINDOW_MANAGER].backend = FakeBackend([win])

    try:
        orchestrator.handle_user_message("close the notepad window", request_id="r1")
        pytest.fail("must still gate a real window")
    except PendingConfirmation as e:
        pending = e
    assert win.calls == []

    orchestrator.resume_with_confirmation(pending.task, approved=True, request_id="r1")
    assert win.calls == ["close"]


def test_ambiguous_close_never_prompts_either(tmp_path):
    orchestrator = create_orchestrator(str(tmp_path / "pf3.db"))
    a, b = FakeWindow("doc1 - Word"), FakeWindow("doc2 - Word")
    orchestrator.agents[AgentName.WINDOW_MANAGER].backend = FakeBackend([a, b])

    results = orchestrator.handle_user_message("close the word window", request_id="r1")
    assert not results[-1].success
    assert "matches 2 windows" in results[-1].output
    assert a.calls == [] and b.calls == []


def test_preflight_default_is_a_noop_for_other_agents():
    """BaseAgent.preflight defaults to None so existing agents are unaffected."""
    from agents.general import GeneralAgent
    import tempfile as tf
    memory = MemoryEngine(store=Store(Path(tf.mkdtemp()) / "g.db"))
    agent = GeneralAgent(memory)
    assert agent.preflight(_task("anything at all")) is None
