import tempfile
from pathlib import Path

import pytest

from core.factory import create_orchestrator
from core.orchestrator import PendingConfirmation


@pytest.fixture
def orchestrator(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        workspace_dir = Path(tmp) / "workspace"
        monkeypatch.setattr("agents.automation_config.WORKSPACE_ROOT", str(workspace_dir))
        yield create_orchestrator(str(Path(tmp) / "test.db")), workspace_dir


def test_read_file_does_not_require_confirmation(orchestrator):
    orch, workspace = orchestrator
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "existing.txt").write_text("already here")

    # SAFE operations should just run -- no PendingConfirmation raised.
    results = orch.handle_user_message("read file existing.txt", request_id="r1")
    assert any(r.success and "already here" in r.output for r in results)


def test_delete_file_requires_confirmation_end_to_end(orchestrator):
    """This is the whole point of building automation on top of the
    existing confirmation infrastructure: a real destructive filesystem
    operation must be gated by the SAME orchestrator-level check already
    proven for the Coding/General agents' (fake) destructive actions."""
    orch, workspace = orchestrator
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "important.txt").write_text("do not lose this")

    with pytest.raises(PendingConfirmation) as exc_info:
        orch.handle_user_message("delete the file important.txt", request_id="r2")

    # File must NOT be deleted yet -- confirmation hasn't happened.
    assert (workspace / "important.txt").exists()

    pending_task = exc_info.value.task
    orch.resume_with_confirmation(pending_task, approved=True, request_id="r2")

    # NOW it should actually be gone -- a REAL effect, not just a claim.
    assert not (workspace / "important.txt").exists()


def test_rejected_delete_confirmation_leaves_file_untouched(orchestrator):
    orch, workspace = orchestrator
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "keep_me.txt").write_text("still here")

    with pytest.raises(PendingConfirmation) as exc_info:
        orch.handle_user_message("delete the file keep_me.txt", request_id="r3")

    pending_task = exc_info.value.task
    results = orch.resume_with_confirmation(pending_task, approved=False, request_id="r3")

    assert (workspace / "keep_me.txt").exists()
    assert "won't do that" in results[0].output.lower()


def test_git_push_requires_confirmation(orchestrator, monkeypatch):
    orch, _workspace = orchestrator
    with pytest.raises(PendingConfirmation) as exc_info:
        orch.handle_user_message("git push", request_id="r4")
    assert exc_info.value.task.risk.value == "destructive"
