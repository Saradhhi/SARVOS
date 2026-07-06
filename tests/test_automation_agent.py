import subprocess
import tempfile
from pathlib import Path

import pytest

from agents.automation import AutomationAgent, PathSafetyError, resolve_safe_path
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str, confirmed: bool = True) -> Task:
    return Task(
        parent_request_id="r1", agent=AgentName.AUTOMATION,
        instruction=instruction, context={"confirmed": confirmed} if confirmed else {},
    )


@pytest.fixture
def workspace(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        workspace_dir = Path(tmp) / "workspace"
        monkeypatch.setattr("agents.automation_config.WORKSPACE_ROOT", str(workspace_dir))
        yield workspace_dir


@pytest.fixture
def agent(workspace):
    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryEngine(store=Store(Path(tmp) / "test.db"))
        yield AutomationAgent(memory)


# ---- Path safety (the critical security boundary) --------------------------

def test_resolve_safe_path_allows_normal_relative_path(workspace):
    result = resolve_safe_path("notes.txt", workspace_root=str(workspace))
    assert result == (workspace.resolve() / "notes.txt")


def test_resolve_safe_path_rejects_parent_traversal(workspace):
    with pytest.raises(PathSafetyError):
        resolve_safe_path("../../etc/passwd", workspace_root=str(workspace))


def test_resolve_safe_path_rejects_absolute_path_elsewhere(workspace):
    with pytest.raises(PathSafetyError):
        resolve_safe_path("/etc/passwd", workspace_root=str(workspace))


def test_resolve_safe_path_allows_nested_subdirectory(workspace):
    result = resolve_safe_path("subdir/file.txt", workspace_root=str(workspace))
    assert workspace.resolve() in result.parents


# ---- File operations (real filesystem, sandboxed to a temp workspace) ------

def test_write_then_read_file(agent, workspace):
    write_result = agent.handle(_task("write a file called notes.txt with hello world"))
    assert write_result.success
    assert (workspace / "notes.txt").read_text() == "hello world"

    read_result = agent.handle(_task("read file notes.txt"))
    assert read_result.success
    assert "hello world" in read_result.output


def test_read_nonexistent_file_fails_gracefully(agent):
    result = agent.handle(_task("read file does_not_exist.txt"))
    assert not result.success
    assert "doesn't exist" in result.output


def test_list_directory(agent, workspace):
    agent.handle(_task("write a file called a.txt with content a"))
    agent.handle(_task("write a file called b.txt with content b"))
    result = agent.handle(_task("list the files in ."))
    assert result.success
    assert "a.txt" in result.output
    assert "b.txt" in result.output


def test_delete_file(agent, workspace):
    agent.handle(_task("write a file called temp.txt with delete me"))
    assert (workspace / "temp.txt").exists()

    result = agent.handle(_task("delete the file temp.txt"))
    assert result.success
    assert not (workspace / "temp.txt").exists()


def test_delete_nonexistent_file_fails_gracefully(agent):
    result = agent.handle(_task("delete the file nope.txt"))
    assert not result.success
    assert "nothing to delete" in result.output


def test_path_traversal_attempt_is_refused_not_executed(agent):
    """Even if something upstream produced a malicious-looking path, the
    agent itself refuses at execution time -- defense in depth."""
    result = agent.handle(_task("read file ../../../etc/passwd"))
    assert not result.success
    assert result.error == "path_safety"


# ---- Move / copy -------------------------------------------------------

def test_move_file_real_effect(agent, workspace):
    agent.handle(_task("write a file called draft.txt with hello"))
    result = agent.handle(_task("move the file draft.txt to archive.txt"))
    assert result.success
    assert not (workspace / "draft.txt").exists()
    assert (workspace / "archive.txt").read_text() == "hello"


def test_rename_is_same_as_move(agent, workspace):
    agent.handle(_task("write a file called old.txt with content"))
    result = agent.handle(_task("rename the file old.txt to new.txt"))
    assert result.success
    assert not (workspace / "old.txt").exists()
    assert (workspace / "new.txt").exists()


def test_copy_file_leaves_source_intact(agent, workspace):
    agent.handle(_task("write a file called source.txt with important data"))
    result = agent.handle(_task("copy the file source.txt to backup.txt"))
    assert result.success
    assert (workspace / "source.txt").read_text() == "important data"
    assert (workspace / "backup.txt").read_text() == "important data"


def test_move_refuses_to_overwrite_existing_destination(agent, workspace):
    """Overwrite protection: even though 'move' was already confirmed as
    DESTRUCTIVE by the user, that consent covered moving the file, not
    silently destroying whatever's already at the destination -- a
    different, un-consented-to loss of data."""
    agent.handle(_task("write a file called source.txt with new content"))
    agent.handle(_task("write a file called dest.txt with original content"))

    result = agent.handle(_task("move the file source.txt to dest.txt"))
    assert not result.success
    assert result.error == "destination_exists"
    # BOTH files must be untouched -- the move must not have partially happened.
    assert (workspace / "source.txt").read_text() == "new content"
    assert (workspace / "dest.txt").read_text() == "original content"


def test_copy_refuses_to_overwrite_existing_destination(agent, workspace):
    agent.handle(_task("write a file called source.txt with new content"))
    agent.handle(_task("write a file called dest.txt with original content"))

    result = agent.handle(_task("copy the file source.txt to dest.txt"))
    assert not result.success
    assert result.error == "destination_exists"
    assert (workspace / "dest.txt").read_text() == "original content"


def test_move_nonexistent_source_fails_gracefully(agent):
    result = agent.handle(_task("move the file ghost.txt to somewhere.txt"))
    assert not result.success
    assert "doesn't exist" in result.output


def test_copy_nonexistent_source_fails_gracefully(agent):
    result = agent.handle(_task("copy the file ghost.txt to somewhere.txt"))
    assert not result.success
    assert "doesn't exist" in result.output


# ---- Git commands (real subprocess, against a real temp git repo) ----------

@pytest.fixture
def git_repo(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
        (Path(tmp) / "README.md").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp, check=True)
        monkeypatch.setattr("agents.automation_config.GIT_REPO_ROOT", tmp)
        yield tmp


def test_git_status_runs_for_real(agent, git_repo):
    result = agent.handle(_task("git status"))
    assert result.success
    assert "git status" in result.output


def test_git_log_runs_for_real(agent, git_repo):
    result = agent.handle(_task("git log"))
    assert result.success
    assert "initial" in result.output  # the real commit message from the fixture


def test_git_disallowed_subcommand_is_refused(agent, git_repo):
    """Defense in depth: even if classify() somehow let something odd
    through, the agent re-checks the allowlist itself before running
    anything via subprocess."""
    result = agent.handle(_task("git something-not-real"))
    assert not result.success
    assert result.error == "git_command_not_allowed"


def test_unrecognized_instruction_gives_helpful_message(agent):
    result = agent.handle(_task("do something with the thing"))
    assert not result.success
    assert "read file" in result.output.lower()  # suggests valid phrasing
