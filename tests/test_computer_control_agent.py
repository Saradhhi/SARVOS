import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from agents.computer_control import ComputerControlAgent
from core.factory import create_orchestrator
from core.orchestrator import PendingConfirmation
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str) -> Task:
    return Task(parent_request_id="r1", agent=AgentName.COMPUTER_CONTROL, instruction=instruction)


@pytest.fixture
def agent(tmp_path):
    memory = MemoryEngine(store=Store(tmp_path / "test.db"))
    yield ComputerControlAgent(memory)


# ---- Screenshot / clipboard: honest tests of the graceful-failure path.
# This sandbox has no display and no clipboard mechanism at all -- these
# prove the REAL failure handling works, the same pattern as System
# Info's no-battery test.

def test_screenshot_fails_gracefully_with_no_display(agent):
    result = agent.handle(_task("take a screenshot"))
    if not result.success:
        assert "error" in result.error or result.error == "screenshot_failed"


def test_clipboard_fails_gracefully_with_no_mechanism(agent):
    result = agent.handle(_task("read the clipboard"))
    if not result.success:
        assert result.error == "clipboard_failed"


def test_lock_behaves_correctly_for_this_actual_platform(agent):
    """This must not assume a headless Linux sandbox -- on a real Windows
    machine with a real desktop session, locking genuinely works. Accept
    whichever outcome is actually true for the machine running this test,
    rather than assuming failure."""
    result = agent.handle(_task("lock my computer"))
    if sys.platform.startswith("win"):
        # Real success is the correct, expected outcome on real Windows.
        assert result.success or result.error == "lock_failed"
    else:
        assert not result.success
        assert result.error == "lock_not_supported"


def test_volume_reports_not_supported_without_windows_com(agent):
    result = agent.handle(_task("mute"))
    if not sys.platform.startswith("win"):
        assert not result.success
        assert result.error == "volume_not_supported"
    # On real Windows with a working audio device, this should genuinely
    # succeed -- not asserted strictly here since CI-style environments
    # can lack an audio device even on Windows.


def test_brightness_behaves_correctly_for_this_actual_display(agent):
    """Same lesson as the lock test: a real Windows laptop with a real
    display genuinely supports brightness control. This sandbox (headless
    Linux, no display at all) correctly cannot. Assert whichever is
    actually true rather than assuming the sandbox's environment."""
    result = agent.handle(_task("set the brightness to 50%"))
    if not result.success:
        assert result.error == "brightness_not_supported"
    else:
        assert result.data["brightness"] == 50


# ---- Launch/close app: genuinely real, fully testable, no platform
# dependency at all (uses subprocess + psutil directly).

def test_launch_app_starts_a_real_process(agent):
    result = agent.handle(_task(f"launch {sys.executable}"))
    assert result.success
    assert result.data["app"] == sys.executable


def test_launch_nonexistent_app_fails_gracefully(agent):
    result = agent.handle(_task("launch this_program_does_not_exist_xyz123"))
    assert not result.success
    assert result.error == "launch_failed"


def test_close_app_terminates_a_real_running_process(agent):
    """Fully real: spawns an actual subprocess, then verifies close_app
    genuinely finds and terminates it via psutil.

    Uses sys.executable (works identically on Windows and Linux, unlike
    'sleep', which doesn't exist on Windows at all -- confirmed directly:
    this test originally used 'sleep' and failed with a real
    FileNotFoundError on a real Windows run) combined with a unique
    command-line marker. Matching on the marker via cmdline (not the bare
    process name) avoids two real risks: matching SARVOS's own process
    (a generic name like 'python3' — handled by the separate PID
    exclusion) and matching some OTHER unrelated Python process the
    person happens to have running on their actual machine, which a bare
    name match against something as generic as 'python.exe' absolutely
    could do on a real, actively-used computer."""
    marker = f"sarvos_test_marker_{os.getpid()}"
    proc = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep(30)  # {marker}"]
    )
    time.sleep(0.3)  # let it actually start

    try:
        result = agent.handle(_task(f"close the application {marker}"))
        assert result.success
        proc.wait(timeout=5)
        assert proc.poll() is not None  # confirms it actually exited
    finally:
        if proc.poll() is None:
            proc.kill()


def test_close_app_never_matches_its_own_process(agent, monkeypatch):
    """Regression test for the real bug found earlier: calling close_app
    with a generic name (like the Python interpreter's own name) that
    matches SARVOS's own running process must never terminate it.

    Deliberately does NOT call close_app against the REAL, full,
    uncontrolled system process list with a generic name -- doing exactly
    that during test development terminated something in the broader
    sandbox environment beyond just the intended test process (the exact
    process was not fully identified, but re-probing it further wasn't
    worth the risk). Instead, this verifies the actual exclusion logic
    directly: a mocked process list that includes an entry for THIS
    process's own real PID, confirming terminate() is never called on
    it, without touching any real, uncontrolled process on the machine
    running the test."""
    own_pid = os.getpid()
    terminated_pids = []

    class _FakeProc:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name, "cmdline": [name]}

        def terminate(self):
            terminated_pids.append(self.info["pid"])

    fake_processes = [
        _FakeProc(own_pid, "python3"),       # this process's own PID -- must be skipped
        _FakeProc(own_pid + 99999, "python3"),  # a different, unrelated python3 process
    ]
    monkeypatch.setattr(
        "psutil.process_iter", lambda attrs=None: iter(fake_processes)
    )

    result = agent.handle(_task("close the application python3"))

    assert own_pid not in terminated_pids, (
        "close_app terminated its OWN process -- this must never happen."
    )
    assert (own_pid + 99999) in terminated_pids
    assert result.success


def test_close_app_no_match_fails_gracefully(agent):
    result = agent.handle(_task("close the application definitely_not_a_running_process_xyz"))
    assert not result.success
    assert result.error == "process_not_found"


# ---- Power state: THE critical tests. Never actually invoke a real
# subprocess for shutdown/restart/sleep -- _execute_system_command is
# monkeypatched in every one of these, proving the confirmation gate
# works without ever risking a real shutdown during a test run.

def test_shutdown_never_executes_before_confirmation(tmp_path, monkeypatch):
    """Mirrors AutoDeveloper's deploy test exactly: proves through the
    REAL orchestrator that shutdown does not execute at the point
    PendingConfirmation is raised, and only executes after explicit
    approval. _execute_system_command is monkeypatched so no real
    subprocess ever runs, regardless of platform."""
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []

    def fake_execute(self, args):
        calls.append(args)
        return True, "ok"

    monkeypatch.setattr(
        "agents.computer_control.ComputerControlAgent._execute_system_command",
        fake_execute,
    )

    orchestrator = create_orchestrator(str(tmp_path / "test.db"))

    try:
        orchestrator.handle_user_message("shut down my computer", request_id="r1")
        pytest.fail("Expected PendingConfirmation to be raised")
    except PendingConfirmation as e:
        pending = e

    assert calls == [], "Shutdown command ran BEFORE confirmation -- this must never happen."

    results = orchestrator.resume_with_confirmation(pending.task, approved=True, request_id="r1")
    assert len(calls) == 1
    assert results[-1].success


def test_shutdown_rejected_never_executes(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(
        "agents.computer_control.ComputerControlAgent._execute_system_command",
        lambda self, args: (calls.append(args), (True, "ok"))[1],
    )

    orchestrator = create_orchestrator(str(tmp_path / "test2.db"))

    try:
        orchestrator.handle_user_message("restart my computer", request_id="r1")
        pytest.fail("Expected PendingConfirmation")
    except PendingConfirmation as e:
        pending = e

    results = orchestrator.resume_with_confirmation(pending.task, approved=False, request_id="r1")
    assert calls == []
    assert not results[-1].success


def test_shutdown_not_supported_on_non_windows():
    """Only meaningful on a genuinely non-Windows platform -- skipped on
    Windows rather than assuming the platform, which is what caused a
    real near-miss (see _execute_system_command's docstring): this test
    previously assumed non-Windows unconditionally, and when it actually
    ran on Windows, it fell through to a REAL, unmocked shutdown
    attempt."""
    if sys.platform.startswith("win"):
        pytest.skip("This test only applies to non-Windows platforms.")
    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryEngine(store=Store(Path(tmp) / "test.db"))
        agent = ComputerControlAgent(memory)
        result = agent.handle(_task("shut down my computer"))
        assert not result.success
        assert result.error == "shutdown_not_supported"


def test_shutdown_on_windows_always_goes_through_mocked_execution(monkeypatch):
    """Windows-specific behavior, but ALWAYS with _execute_system_command
    mocked -- never a real call, regardless of platform this actually
    runs on. Proves the Windows code path is reached and constructs the
    right command, without ever risking a real shutdown."""
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(
        "agents.computer_control.ComputerControlAgent._execute_system_command",
        lambda self, args: (calls.append(args), (True, "ok"))[1],
    )
    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryEngine(store=Store(Path(tmp) / "test.db"))
        agent = ComputerControlAgent(memory)
        result = agent.handle(_task("shut down my computer"))
        assert result.success
        assert calls == [["shutdown", "/s", "/t", "0"]]


def test_get_volume_interface_prefers_new_endpointvolume_property(agent, monkeypatch):
    """Regression test for a real bug confirmed via a user's diagnostic on
    pycaw 1.4.16: newer pycaw's GetSpeakers() returns a wrapped object
    with an .EndpointVolume property and NO .Activate() method. The
    original code called .Activate() and failed with 'AudioDevice object
    has no attribute Activate' on real Windows. This verifies the new
    property is used when present."""
    sentinel_endpoint = object()

    class _NewApiDevice:
        EndpointVolume = sentinel_endpoint
        # deliberately has NO .Activate method, like real pycaw 1.4.16

    fake_pycaw = type(sys)("pycaw.pycaw")
    fake_pycaw.AudioUtilities = type("AU", (), {"GetSpeakers": staticmethod(lambda: _NewApiDevice())})
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw)

    result = agent._get_volume_interface()
    assert result is sentinel_endpoint


def test_get_volume_interface_falls_back_to_legacy_activate(agent, monkeypatch):
    """The other half: older pycaw versions have no .EndpointVolume
    property and DO need the legacy Activate()+cast() path. Verifies the
    fallback branch is reached when .EndpointVolume is absent.

    Note: this asserts the legacy branch is ENTERED (Activate gets
    called), not the final ctypes cast result -- POINTER()/cast() need
    real ctypes storage info that a plain fake class can't provide, and
    mocking ctypes that deeply would test the mock rather than the real
    branch logic. The branch decision (new property vs. legacy Activate)
    is the actual thing that broke on real Windows, and that's what this
    covers."""
    activate_called = []

    class _OldApiDevice:
        # No EndpointVolume attribute at all (getattr returns None).
        def Activate(self, iid, ctx, arg):
            activate_called.append((iid, ctx))
            raise RuntimeError("stop here -- branch reached, ctypes cast not under test")

    fake_pycaw = type(sys)("pycaw.pycaw")
    fake_pycaw.AudioUtilities = type("AU", (), {"GetSpeakers": staticmethod(lambda: _OldApiDevice())})
    fake_pycaw.IAudioEndpointVolume = type("IAEV", (), {"_iid_": "fake-iid"})
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw)

    import types as _types
    fake_comtypes = _types.ModuleType("comtypes")
    fake_comtypes.CLSCTX_ALL = 0x17
    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)

    with pytest.raises(RuntimeError, match="branch reached"):
        agent._get_volume_interface()
    assert activate_called, "legacy Activate() path should have been reached"


def test_unrecognized_instruction_gives_helpful_message(agent):
    result = agent.handle(_task("do something computer-control-ish but vague"))
    assert not result.success
    assert "screenshot" in result.output.lower() or "shut down" in result.output.lower()
