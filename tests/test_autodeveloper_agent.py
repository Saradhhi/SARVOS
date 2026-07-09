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


# ---- Auto-heal: propose (SAFE) then apply (DESTRUCTIVE) -----------------
#
# The original integration had a `simulate_llm_patch` stub that returned a
# hardcoded fake test and wrote it to disk AUTOMATICALLY, before any
# confirmation. These tests prove all three of those things are inverted:
# the LLM call is real (mocked here, but a real interface), nothing is
# written during propose, and apply is gated by the real orchestrator.

class _FakeLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def generate(self, prompt, system=None):
        self.prompts.append((prompt, system))
        return self.response

    def is_available(self):
        return True


def _write_failing_project(workspace, sys_executable):
    """A real, tiny project whose test genuinely fails."""
    (workspace / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (workspace / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n"
    )


def test_propose_fix_writes_nothing_and_shows_a_diff(agent, workspace, monkeypatch):
    """THE critical test: propose is SAFE and must not touch the disk."""
    import sys
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )

    fixed = "def add(a, b):\n    return a + b\n"
    fake = _FakeLLM(fixed)
    monkeypatch.setattr("agents.autodeveloper.get_llm_client", lambda: fake)

    before = (workspace / "calc.py").read_text()
    result = agent.handle(_task("propose a fix for calc.py"))

    assert result.success
    # Nothing written -- the file on disk is byte-for-byte unchanged.
    assert (workspace / "calc.py").read_text() == before
    # A real unified diff was shown.
    assert "-    return a - b" in result.output
    assert "+    return a + b" in result.output
    assert "NOT applied" in result.output
    # The LLM genuinely saw the real file contents and real test output.
    prompt, _system = fake.prompts[0]
    assert "return a - b" in prompt
    assert "assert add(2, 2) == 4" in prompt or "test_add" in prompt


def test_apply_fix_without_a_proposal_refuses(agent):
    result = agent.handle(_task("apply the fix"))
    assert not result.success
    assert result.error == "no_pending_patch"


def test_propose_then_apply_writes_the_reviewed_patch(agent, workspace, monkeypatch):
    import sys
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )
    fixed = "def add(a, b):\n    return a + b\n"
    monkeypatch.setattr("agents.autodeveloper.get_llm_client", lambda: _FakeLLM(fixed))

    agent.handle(_task("propose a fix for calc.py"))
    result = agent.handle(_task("apply the fix"))

    assert result.success
    assert (workspace / "calc.py").read_text() == fixed


def test_apply_refuses_if_file_changed_since_proposal(agent, workspace, monkeypatch):
    """Real safety guard: if the file changed between propose and apply,
    the diff the person approved no longer describes reality."""
    import sys
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )
    monkeypatch.setattr(
        "agents.autodeveloper.get_llm_client",
        lambda: _FakeLLM("def add(a, b):\n    return a + b\n"),
    )

    agent.handle(_task("propose a fix for calc.py"))
    # Someone edits the file after the proposal was reviewed.
    (workspace / "calc.py").write_text("def add(a, b):\n    return 999\n")

    result = agent.handle(_task("apply the fix"))
    assert not result.success
    assert result.error == "file_changed_since_proposal"
    # The stale patch was discarded, not silently applied.
    assert (workspace / "calc.py").read_text() == "def add(a, b):\n    return 999\n"


def test_propose_fix_when_tests_pass_does_nothing(agent, workspace, monkeypatch):
    import sys
    (workspace / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (workspace / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n"
    )
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )
    result = agent.handle(_task("propose a fix"))
    assert result.success
    assert "already pass" in result.output.lower()


def test_propose_fix_handles_llm_unavailable(agent, workspace, monkeypatch):
    import sys
    from llm.client import LLMUnavailable
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )

    def unavailable():
        class _Dead:
            def generate(self, prompt, system=None):
                raise LLMUnavailable("Ollama isn't running")
        return _Dead()

    monkeypatch.setattr("agents.autodeveloper.get_llm_client", unavailable)
    result = agent.handle(_task("propose a fix for calc.py"))
    assert not result.success
    assert result.error == "llm_unavailable"


def test_propose_fix_respects_cannot_determine_fix(agent, workspace, monkeypatch):
    import sys
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )
    monkeypatch.setattr(
        "agents.autodeveloper.get_llm_client", lambda: _FakeLLM("CANNOT_DETERMINE_FIX")
    )
    result = agent.handle(_task("propose a fix for calc.py"))
    assert not result.success
    assert result.error == "no_fix_proposed"


def test_strips_markdown_fences_from_llm_response(agent, workspace, monkeypatch):
    """Models often wrap code in fences despite instructions. Writing
    ```python into a source file would be a real bug."""
    import sys
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )
    fenced = "```python\ndef add(a, b):\n    return a + b\n```"
    monkeypatch.setattr("agents.autodeveloper.get_llm_client", lambda: _FakeLLM(fenced))

    agent.handle(_task("propose a fix for calc.py"))
    agent.handle(_task("apply the fix"))
    written = (workspace / "calc.py").read_text()
    assert "```" not in written
    assert written.strip() == "def add(a, b):\n    return a + b"


def test_apply_fix_is_gated_by_the_real_orchestrator(tmp_path, workspace, monkeypatch):
    """Proves through the REAL orchestrator that apply_fix is DESTRUCTIVE:
    the file must be untouched when PendingConfirmation is raised, and only
    written after explicit approval. Mirrors the deploy-gating test."""
    import sys
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )
    fixed = "def add(a, b):\n    return a + b\n"
    monkeypatch.setattr("agents.autodeveloper.get_llm_client", lambda: _FakeLLM(fixed))

    orchestrator = create_orchestrator(str(tmp_path / "heal.db"))
    # propose is SAFE -- runs without gating.
    orchestrator.handle_user_message("propose a fix for calc.py", request_id="r1")
    before = (workspace / "calc.py").read_text()

    try:
        orchestrator.handle_user_message("apply the fix", request_id="r1")
        pytest.fail("Expected apply the fix to be gated")
    except PendingConfirmation as e:
        pending = e

    assert (workspace / "calc.py").read_text() == before, (
        "Patch written BEFORE confirmation -- must never happen."
    )

    results = orchestrator.resume_with_confirmation(pending.task, approved=True, request_id="r1")
    assert results[-1].success
    assert (workspace / "calc.py").read_text() == fixed


def test_propose_without_a_file_recommends_one_and_writes_nothing(agent, workspace, monkeypatch):
    """Real, fundamental limitation confirmed by running pytest for real:
    default pytest output often names only the TEST file, never the buggy
    source file. So the agent must RECOMMEND a target and stop, rather than
    guess and risk overwriting the wrong file."""
    import sys
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )
    before = (workspace / "calc.py").read_text()

    result = agent.handle(_task("propose a fix"))
    assert result.success
    assert result.data["needs_target"] is True
    assert "calc.py" in result.output
    assert "won't guess" in result.output.lower()
    # Nothing written, and no LLM call was even needed.
    assert (workspace / "calc.py").read_text() == before


def test_refuses_to_patch_a_test_file_even_if_asked(agent, workspace, monkeypatch):
    """Making a failing test pass by rewriting the test is almost never the
    fix -- and silently doing so is exactly how the original integration's
    stub clobbered a test file. Refuse explicitly, even on direct request."""
    import sys
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )
    before = (workspace / "test_calc.py").read_text()

    result = agent.handle(_task("propose a fix for test_calc.py"))
    assert not result.success
    assert result.error == "refused_test_file"
    assert (workspace / "test_calc.py").read_text() == before


def test_refuses_a_file_outside_the_workspace(agent, workspace, monkeypatch):
    import sys
    _write_failing_project(workspace, sys.executable)
    monkeypatch.setattr(
        "agents.autodeveloper_config.TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )
    result = agent.handle(_task("propose a fix for ../../etc/passwd.py"))
    assert not result.success
    assert result.error in {"unsafe_path", "file_not_found"}
