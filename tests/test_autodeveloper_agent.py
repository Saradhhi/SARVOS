import tempfile
from pathlib import Path

import pytest

from agents.autodeveloper import AutoDeveloperAgent
from core.factory import create_orchestrator
from core.orchestrator import PendingConfirmation
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str) -> Task:
    return Task(parent_request_id="r1", agent=AgentName.AUTODEVELOPER, instruction=instruction)


@pytest.fixture
def workspace(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        workspace_dir = Path(tmp) / "workspace"
        workspace_dir.mkdir()
        monkeypatch.setattr("agents.autodeveloper_config.WORKSPACE_ROOT", str(workspace_dir))
        yield workspace_dir


@pytest.fixture
def agent(tmp_path, workspace):
    memory = MemoryEngine(store=Store(tmp_path / "test.db"))
    yield AutoDeveloperAgent(memory)


def test_analyze_lists_real_files(agent, workspace):
    (workspace / "main.py").write_text("print('hello')")
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "helper.py").write_text("pass")

    result = agent.handle(_task("analyze the workspace"))
    assert result.success
    assert "main.py" in result.output
    assert "subdir" in result.output
    assert "helper.py" in result.output
    assert result.data["entry_count"] == 3


def test_analyze_empty_workspace(agent, workspace):
    result = agent.handle(_task("analyze the workspace"))
    assert result.success
    assert "empty" in result.output.lower()


def test_run_tests_executes_real_subprocess_and_succeeds(agent, monkeypatch):
    import sys
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND",
        f'{sys.executable} -c "print(1)"',
    )
    result = agent.handle(_task("run the tests"))
    assert result.success
    assert result.data["returncode"] == 0


def test_run_tests_reports_real_failure(agent, monkeypatch):
    import sys
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND",
        f'{sys.executable} -c "import sys; sys.exit(1)"',
    )
    result = agent.handle(_task("run the tests"))
    assert not result.success
    assert result.data["returncode"] == 1


def test_deploy_command_never_runs_before_confirmation(tmp_path, workspace, monkeypatch):
    """THE critical regression test for the actual bug found in the
    original integration: execution happened before the 'confirmation'
    prompt was ever shown. Proves the real fix end-to-end through the
    real orchestrator: a deploy command that would create a marker file
    must NOT have run yet at the point PendingConfirmation is raised --
    and must only run after resume_with_confirmation(approved=True)."""
    import sys

    marker = workspace / "deployed.marker"
    deploy_cmd = f'{sys.executable} -c "open(\'{marker.as_posix()}\', \'w\').close()"'
    monkeypatch.setattr("agents.autodeveloper_config.DEPLOY_COMMAND", deploy_cmd)

    orchestrator = create_orchestrator(str(tmp_path / "test.db"))

    pending = None
    try:
        orchestrator.handle_user_message("deploy the project", request_id="r1")
        pytest.fail("Expected PendingConfirmation to be raised")
    except PendingConfirmation as e:
        pending = e

    # The critical assertion: nothing has executed yet.
    assert not marker.exists(), (
        "Deploy command ran BEFORE confirmation -- this is exactly the "
        "bug the rebuild was meant to fix."
    )

    results = orchestrator.resume_with_confirmation(pending.task, approved=True, request_id="r1")

    # Only NOW, after explicit approval, should the command have run.
    assert marker.exists(), "Deploy command should have run after approval, but didn't."
    assert results[-1].success


def test_deploy_rejected_never_executes(tmp_path, workspace, monkeypatch):
    import sys

    marker = workspace / "deployed.marker"
    deploy_cmd = f'{sys.executable} -c "open(\'{marker.as_posix()}\', \'w\').close()"'
    monkeypatch.setattr("agents.autodeveloper_config.DEPLOY_COMMAND", deploy_cmd)

    orchestrator = create_orchestrator(str(tmp_path / "test2.db"))

    try:
        orchestrator.handle_user_message("deploy the project", request_id="r1")
        pytest.fail("Expected PendingConfirmation")
    except PendingConfirmation as e:
        pending = e

    results = orchestrator.resume_with_confirmation(pending.task, approved=False, request_id="r1")
    assert not marker.exists()
    assert not results[-1].success
    assert "won't" in results[-1].output.lower()


def test_unrecognized_instruction_gives_helpful_message(agent):
    result = agent.handle(_task("do something autodeveloper-ish but vague"))
    assert not result.success
    assert "workspace" in result.output.lower() or "deploy" in result.output.lower()
