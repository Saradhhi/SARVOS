"""
AutoDeveloperAgent -- rebuilt from a reviewed integration that had two real
problems: (1) execution happened BEFORE the "confirmation" prompt was ever
shown (the prompt was just text describing something already done), and
(2) file-path safety used a string-prefix check vulnerable to sibling-
directory bypass ("workspace_evil/" satisfies startswith("workspace")
without being inside it).

Both fixed here:
1. RUN_TESTS and DEPLOY are DESTRUCTIVE (agents/autodeveloper_intent.py) --
   the orchestrator's real confirmation gate (core/orchestrator.py) runs
   BEFORE this agent's handle() is ever called for those operations. By
   the time a subprocess actually executes, the user has already said yes.
2. No user-supplied file paths anywhere in this agent -- workspace
   analysis and commands all operate on a FIXED, admin-configured
   workspace root, resolved via the SAME tested resolve_safe_path()
   already used by agents/automation.py (proper parent-directory
   membership, not string-prefix matching).

Deliberately NOT included: the original's automatic "self-healing" loop,
which called a hardcoded stub (`simulate_llm_patch`, not a real patch
generator) and wrote its fake output directly to a test file, up to twice,
automatically, before any confirmation at all. A real auto-heal feature
(wired to Ollama, showing you the actual proposed diff before writing)
is a legitimate future addition -- built and tested on its own, the same
way everything else in this project has been -- not something to carry
over as-is from a stub.
"""

from __future__ import annotations

import shlex
import subprocess
import sys

from agents import autodeveloper_config
from agents.automation import resolve_safe_path
from agents.autodeveloper_intent import Operation, classify
from agents.base import BaseAgent
from core.schemas import AgentName, AgentResult, Task


class AutoDeveloperAgent(BaseAgent):
    name = AgentName.AUTODEVELOPER

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        if intent.operation == Operation.ANALYZE:
            return self._analyze(task)
        if intent.operation == Operation.RUN_TESTS:
            return self._run_command(task, autodeveloper_config.TEST_COMMAND, "tests")
        if intent.operation == Operation.DEPLOY:
            return self._run_command(task, autodeveloper_config.DEPLOY_COMMAND, "deployment")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=False,
            output=(
                f"I couldn't work out an AutoDeveloper action from: "
                f"'{task.instruction}'. Try 'analyze the workspace', "
                f"'run the tests', or 'deploy the project'."
            ),
        )

    def _analyze(self, task: Task) -> AgentResult:
        root = resolve_safe_path(".", workspace_root=autodeveloper_config.WORKSPACE_ROOT)
        entries = []
        for path in sorted(root.rglob("*")):
            depth = len(path.relative_to(root).parts) - 1
            indent = "    " * depth
            suffix = "/" if path.is_dir() else ""
            entries.append(f"{indent}{path.name}{suffix}")

        if not entries:
            output = f"Workspace ({root}) is empty."
        else:
            output = f"Workspace contents ({root}):\n" + "\n".join(entries)

        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True, output=output,
            data={"workspace": str(root), "entry_count": len(entries)},
        )

    def _run_command(self, task: Task, command_str: str, label: str) -> AgentResult:
        """Runs an admin-configured command (test or deploy) -- by the
        time this method is called for a DESTRUCTIVE task, the
        orchestrator has already gated it behind a real user confirmation
        (see this module's docstring). command_str is never user-supplied
        text, only what's configured in autodeveloper_config."""
        try:
            use_shell = sys.platform.startswith("win")
            if use_shell:
                result = subprocess.run(
                    command_str, capture_output=True, text=True,
                    timeout=autodeveloper_config.COMMAND_TIMEOUT_SECONDS, shell=True,
                    cwd=str(resolve_safe_path(".", workspace_root=autodeveloper_config.WORKSPACE_ROOT)),
                )
            else:
                args = shlex.split(command_str)
                result = subprocess.run(
                    args, capture_output=True, text=True,
                    timeout=autodeveloper_config.COMMAND_TIMEOUT_SECONDS, shell=False,
                    cwd=str(resolve_safe_path(".", workspace_root=autodeveloper_config.WORKSPACE_ROOT)),
                )
        except subprocess.TimeoutExpired:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"{label.capitalize()} command timed out after "
                       f"{autodeveloper_config.COMMAND_TIMEOUT_SECONDS}s.",
                error="autodeveloper_timeout",
            )
        except FileNotFoundError as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't run {label} command: {e}",
                error="autodeveloper_command_not_found",
            )

        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip() or "(no output)"
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=(result.returncode == 0),
            output=f"$ {command_str}\n{output}",
            data={"returncode": result.returncode, "label": label},
        )
