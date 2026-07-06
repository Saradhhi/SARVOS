"""
AutomationAgent — the first agent in this build with REAL side effects on
the filesystem and via subprocess, rather than just generating text. This
is exactly the gap the spec's original confirmation-gating design was
built for, and exactly what was missing when earlier testing showed the
LLM claiming to have "cleared history" or "deleted files" without actually
doing anything -- those were CodingAgent/GeneralAgent text responses with
no real effect behind them. This agent has real effects, gated by the
orchestrator's centralized confirmation check (see core/orchestrator.py),
using risk levels the Planner assigns via agents/automation_intent.py's
classify() -- the same classification function, so there's no way for the
Planner's risk assessment and this agent's execution to disagree about
what an instruction means.

Safety boundaries, layered:
1. Path sandboxing: ALL file operations are resolved against
   automation_config.WORKSPACE_ROOT and rejected if they'd escape it
   (via ../ traversal, an absolute path elsewhere, or a symlink pointing
   outside). This is enforced at path-resolution time, not just hoped for.
2. Git allowlist: only explicitly recognized subcommands run at all
   (checked AGAIN here, not just trusted from classify() -- defense in
   depth in case classify() and this agent's dispatch ever drift, or a
   Task is constructed some other way that bypasses the Planner).
3. Confirmation gating: SENSITIVE/DESTRUCTIVE operations never reach
   `handle()` at all without the orchestrator having already confirmed
   with the user -- by the time this agent runs, either the operation was
   SAFE or the user already said yes.
4. Size/timeout caps: MAX_FILE_SIZE_BYTES and GIT_TIMEOUT_SECONDS prevent
   resource exhaustion (huge file reads, hung git processes).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agents import automation_config
from agents.automation_intent import (
    GIT_DESTRUCTIVE_SUBCOMMANDS,
    GIT_SAFE_SUBCOMMANDS,
    GIT_SENSITIVE_SUBCOMMANDS,
    Operation,
    classify,
)
from agents.base import BaseAgent
from core.schemas import AgentName, AgentResult, Task


class PathSafetyError(Exception):
    """Raised when a requested path would escape the sandboxed workspace."""


def resolve_safe_path(relative_path: str, workspace_root: str | None = None) -> Path:
    """Resolves relative_path against workspace_root and REFUSES it if the
    resolved, canonicalized path would fall outside workspace_root --
    blocking both '../../../etc/passwd'-style traversal and absolute paths
    that point elsewhere entirely.

    workspace_root defaults to automation_config.WORKSPACE_ROOT, looked up
    dynamically (NOT bound as a stale default at import time) specifically
    so tests can monkeypatch automation_config.WORKSPACE_ROOT and have it
    actually take effect. An earlier version of this code imported
    WORKSPACE_ROOT as a bare name and used it as a function default value
    -- Python evaluates default arguments once, at function definition,
    so monkeypatching the module attribute afterward silently had no
    effect at all. Every write/read/delete operation in this file was
    quietly operating on the real default directory instead of the test's
    temp workspace until this was caught by the tests actually failing on
    a FileNotFoundError instead of passing for the wrong reason."""
    if workspace_root is None:
        workspace_root = automation_config.WORKSPACE_ROOT

    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    candidate = (root / relative_path).resolve()

    # Path.resolve() collapses ".." components AND resolves symlinks, so
    # this check catches both traversal attempts and a symlink inside the
    # workspace that points outside it.
    if root != candidate and root not in candidate.parents:
        raise PathSafetyError(
            f"'{relative_path}' would resolve outside the sandboxed "
            f"workspace ({root}). Refusing."
        )
    return candidate


class AutomationAgent(BaseAgent):
    name = AgentName.AUTOMATION

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)

        try:
            if intent.operation == Operation.READ_FILE:
                return self._read_file(task, intent.path)
            if intent.operation == Operation.LIST_DIR:
                return self._list_dir(task, intent.path)
            if intent.operation == Operation.WRITE_FILE:
                return self._write_file(task, intent.path, intent.content)
            if intent.operation == Operation.DELETE_FILE:
                return self._delete_file(task, intent.path)
            if intent.operation == Operation.MOVE_FILE:
                return self._move_file(task, intent.path, intent.dest_path)
            if intent.operation == Operation.COPY_FILE:
                return self._copy_file(task, intent.path, intent.dest_path)
            if intent.operation == Operation.GIT_COMMAND:
                return self._run_git(task, intent.git_args)
        except PathSafetyError as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Refused: {e}", error="path_safety",
            )

        return AgentResult(
            task_id=task.task_id, agent=self.name, success=False,
            output=(
                f"I couldn't work out a specific file or git action from: "
                f"'{task.instruction}'. Try phrasing like 'read file "
                f"notes.txt', 'list files in projects', 'write a file "
                f"called todo.txt with buy milk', 'move the file a.txt to "
                f"b.txt', 'copy the file a.txt to backup.txt', or 'git status'."
            ),
        )

    # ---- File operations (sandboxed to WORKSPACE_ROOT) --------------------

    def _read_file(self, task: Task, path: str) -> AgentResult:
        target = resolve_safe_path(path)
        if not target.exists():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'{path}' doesn't exist in the workspace.",
            )
        if target.stat().st_size > automation_config.MAX_FILE_SIZE_BYTES:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'{path}' is larger than the {automation_config.MAX_FILE_SIZE_BYTES}-byte read limit.",
            )
        content = target.read_text(errors="replace")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Contents of '{path}':\n{content}",
            data={"path": str(target), "content": content},
        )

    def _list_dir(self, task: Task, path: str) -> AgentResult:
        target = resolve_safe_path(path)
        if not target.exists():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'{path}' doesn't exist in the workspace.",
            )
        if not target.is_dir():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'{path}' is a file, not a directory.",
            )
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
        listing = "\n".join(entries) if entries else "(empty)"
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Contents of '{path}':\n{listing}",
            data={"path": str(target), "entries": entries},
        )

    def _write_file(self, task: Task, path: str, content: str) -> AgentResult:
        target = resolve_safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Wrote {len(content)} characters to '{path}'.",
            data={"path": str(target)},
        )

    def _delete_file(self, task: Task, path: str) -> AgentResult:
        target = resolve_safe_path(path)
        if not target.exists():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'{path}' doesn't exist in the workspace -- nothing to delete.",
            )
        target.unlink()
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Deleted '{path}'.",
            data={"path": str(target)},
        )

    def _move_file(self, task: Task, path: str, dest_path: str) -> AgentResult:
        source = resolve_safe_path(path)
        dest = resolve_safe_path(dest_path)

        if not source.exists():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'{path}' doesn't exist in the workspace.",
            )
        # Overwrite protection: refuse rather than silently clobber,
        # regardless of risk tier already having been confirmed. The
        # confirmation the user gave was for "move this file", not
        # "move this file AND destroy whatever's already at the
        # destination" -- those are different amounts of consent.
        if dest.exists():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"'{dest_path}' already exists. Delete it first or "
                    f"choose a different destination name."
                ),
                error="destination_exists",
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Moved '{path}' to '{dest_path}'.",
            data={"source": str(source), "dest": str(dest)},
        )

    def _copy_file(self, task: Task, path: str, dest_path: str) -> AgentResult:
        source = resolve_safe_path(path)
        dest = resolve_safe_path(dest_path)

        if not source.exists():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'{path}' doesn't exist in the workspace.",
            )
        if dest.exists():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"'{dest_path}' already exists. Delete it first or "
                    f"choose a different destination name."
                ),
                error="destination_exists",
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(dest))
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Copied '{path}' to '{dest_path}'.",
            data={"source": str(source), "dest": str(dest)},
        )

    # ---- Git (allowlisted subcommands only) --------------------------------

    def _run_git(self, task: Task, git_args: list[str]) -> AgentResult:
        subcommand = git_args[0] if git_args else ""
        allowed = (
            GIT_SAFE_SUBCOMMANDS | GIT_SENSITIVE_SUBCOMMANDS | GIT_DESTRUCTIVE_SUBCOMMANDS
        )
        # Re-checked here, not just trusted from classify() -- defense in
        # depth per this module's docstring.
        if subcommand not in allowed:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"'git {subcommand}' isn't an allowed command. Allowed: "
                    f"{sorted(allowed)}"
                ),
                error="git_command_not_allowed",
            )
        try:
            result = subprocess.run(
                ["git"] + git_args,
                cwd=automation_config.GIT_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=automation_config.GIT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'git {' '.join(git_args)}' timed out after "
                       f"{automation_config.GIT_TIMEOUT_SECONDS}s.",
                error="git_timeout",
            )
        except FileNotFoundError:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output="git isn't installed or isn't on PATH.",
                error="git_not_found",
            )

        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=(result.returncode == 0),
            output=f"$ git {' '.join(git_args)}\n{output}",
            data={"returncode": result.returncode},
        )
