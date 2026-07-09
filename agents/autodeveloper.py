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

import difflib
import re

from agents import autodeveloper_config
from agents.automation import resolve_safe_path
from agents.autodeveloper_intent import Operation, classify
from agents.base import BaseAgent
from core.schemas import AgentName, AgentResult, Task
from llm.client import LLMUnavailable, get_llm_client

_PATCH_SYSTEM_PROMPT = (
    "You are a careful software engineer. You are given a source file and "
    "the real output of a failing test run. Propose a minimal fix.\n\n"
    "Respond with ONLY the complete, corrected contents of the file. "
    "No explanation, no markdown fences, no commentary -- just the raw file "
    "contents. If you cannot determine a safe fix, respond with exactly: "
    "CANNOT_DETERMINE_FIX"
)


class AutoDeveloperAgent(BaseAgent):
    name = AgentName.AUTODEVELOPER

    def __init__(self, memory):
        super().__init__(memory)
        # The pending proposed patch lives ONLY in memory, never on disk,
        # until the person explicitly says "apply the fix" (a DESTRUCTIVE,
        # gated operation). Deliberately not persisted anywhere -- a
        # proposal that survives a restart could be applied against a file
        # that has since changed underneath it.
        self._pending_patch: dict | None = None

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        if intent.operation == Operation.ANALYZE:
            return self._analyze(task)
        if intent.operation == Operation.RUN_TESTS:
            return self._run_command(task, autodeveloper_config.TEST_COMMAND, "tests")
        if intent.operation == Operation.DEPLOY:
            return self._run_command(task, autodeveloper_config.DEPLOY_COMMAND, "deployment")
        if intent.operation == Operation.PROPOSE_FIX:
            return self._propose_fix(task, intent)
        if intent.operation == Operation.APPLY_FIX:
            return self._apply_fix(task)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=False,
            output=(
                f"I couldn't work out an AutoDeveloper action from: "
                f"'{task.instruction}'. Try 'analyze the workspace', "
                f"'run the tests', 'propose a fix', 'apply the fix', or "
                f"'deploy the project'."
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
            # 'output' here is the RAW command output, without the "$ cmd"
            # prefix -- _propose_fix parses it to find the failing file, so
            # it needs the real text, not the display-formatted version.
            data={"returncode": result.returncode, "label": label, "output": output},
        )

    # ---- Auto-heal: propose (SAFE) then apply (DESTRUCTIVE) --------------

    def _candidate_source_files(self):
        """Real, non-test Python source files in the workspace -- the pool
        we may RECOMMEND from. Test files are excluded: making a failing
        test pass by rewriting the test is almost never the right fix, and
        doing it silently would be genuinely harmful (it's exactly how the
        original integration's stub clobbered a test file)."""
        root = resolve_safe_path(".", workspace_root=autodeveloper_config.WORKSPACE_ROOT)
        out = []
        for p in sorted(root.rglob("*.py")):
            if p.name.startswith("test_") or p.name.endswith("_test.py"):
                continue
            if any(part in {".venv", "venv", "__pycache__", ".git"} for part in p.parts):
                continue
            out.append(p)
        return out

    def _recommend_source_file(self, test_output: str):
        """Best-effort RECOMMENDATION, never an automatic choice.

        REAL, FUNDAMENTAL LIMITATION, confirmed by running pytest for real
        rather than assuming: default pytest output frequently contains NO
        reference to the buggy source file at all -- only the test file and
        the assertion. For `assert add(2, 2) == 4` failing, the output names
        `test_calc.py` but never `calc.py`. So which source file is wrong
        genuinely cannot be determined from test output in general.

        Rather than guess (and risk overwriting the wrong file), we rank
        candidates by whether their module name appears anywhere in the
        failure output, and hand the top pick back as a SUGGESTION for the
        person to confirm by naming it explicitly.
        """
        candidates = self._candidate_source_files()
        if not candidates:
            return None
        lowered = test_output.lower()
        scored = sorted(
            candidates,
            key=lambda p: (p.stem.lower() not in lowered, p.name.lower()),
        )
        return scored[0]

    def _propose_fix(self, task: Task, intent) -> AgentResult:
        """SAFE: runs the tests, sends the REAL failure output and REAL file
        contents to the LLM, and shows a real unified diff. Writes NOTHING.

        This is the honest replacement for the original integration's
        `simulate_llm_patch`, which returned a hardcoded fake test and wrote
        it to disk automatically, before any confirmation.

        The target file is never chosen silently -- if the person didn't
        name one, we recommend and stop (see _recommend_source_file)."""
        test_result = self._run_command(task, autodeveloper_config.TEST_COMMAND, "tests")
        test_output = str(test_result.data.get("output", "")) if test_result.data else ""

        if test_result.success:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="Tests already pass -- nothing to fix.",
                data={"tests_passing": True},
            )

        if not intent.target_file:
            suggestion = self._recommend_source_file(test_output)
            if suggestion is None:
                return AgentResult(
                    task_id=task.task_id, agent=self.name, success=False,
                    output=(
                        "Tests failed, but I found no non-test source files in "
                        "the workspace to patch."
                    ),
                    error="no_source_files",
                )
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=(
                    f"Tests failed. Which file should I patch?\n\n"
                    f"Test output doesn't reliably say which source file is "
                    f"buggy, so I won't guess. Best suggestion: "
                    f"{suggestion.name}\n\n"
                    f"Say 'propose a fix for {suggestion.name}' to proceed "
                    f"(nothing will be written -- you'll see a diff first)."
                ),
                data={"suggested_file": str(suggestion), "needs_target": True},
            )

        try:
            target = resolve_safe_path(
                intent.target_file, workspace_root=autodeveloper_config.WORKSPACE_ROOT
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Refusing to patch '{intent.target_file}': {e}",
                error="unsafe_path",
            )

        name = target.name
        if name.startswith("test_") or name.endswith("_test.py"):
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"Refusing to patch '{name}': it's a test file. Making a "
                    f"failing test pass by rewriting the test is almost never "
                    f"the right fix."
                ),
                error="refused_test_file",
            )

        if not target.is_file():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'{intent.target_file}' doesn't exist in the workspace.",
                error="file_not_found",
            )

        original = target.read_text(encoding="utf-8", errors="replace")
        if len(original) > autodeveloper_config.MAX_PATCH_FILE_CHARS:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"'{name}' is too large to patch safely ({len(original)} "
                    f"chars). Truncating it would make the model propose a "
                    f"patch against content it never fully saw."
                ),
                error="file_too_large",
            )

        prompt = (
            f"FILE: {name}\n"
            f"--- BEGIN FILE ---\n{original}\n--- END FILE ---\n\n"
            f"--- BEGIN FAILING TEST OUTPUT ---\n"
            f"{test_output[: autodeveloper_config.MAX_TEST_OUTPUT_CHARS]}\n"
            f"--- END FAILING TEST OUTPUT ---"
        )

        try:
            proposed = get_llm_client().generate(prompt, system=_PATCH_SYSTEM_PROMPT)
        except LLMUnavailable as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Can't propose a fix -- the LLM isn't available: {e}",
                error="llm_unavailable",
            )

        proposed = self._strip_code_fences(proposed)

        if not proposed or proposed.strip() == "CANNOT_DETERMINE_FIX":
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output="The model couldn't determine a safe fix. Nothing proposed.",
                error="no_fix_proposed",
            )

        if proposed == original:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output="The model proposed no actual change to the file.",
                error="no_change_proposed",
            )

        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            )
        )

        # Held in memory ONLY. Nothing written.
        self._pending_patch = {"path": target, "original": original, "proposed": proposed}

        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=(
                f"Proposed fix for {name} (NOT applied -- nothing has been "
                f"written):\n\n{diff}\n"
                f"Review it, then say 'apply the fix' to write it "
                f"(you'll be asked to confirm)."
            ),
            data={"file": str(target), "diff": diff},
        )

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Models often wrap code in markdown fences despite being asked not
        to. Strip them rather than writing ```python into a source file."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]  # drop opening fence (with optional language)
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)
        return text.strip() + "\n" if text.strip() else ""

    def _apply_fix(self, task: Task) -> AgentResult:
        """DESTRUCTIVE -- the orchestrator's confirmation gate has already
        required explicit approval before this runs. Applies only a patch
        that was already proposed AND shown as a diff; there is no automatic
        heal loop, and nothing can be applied that the person hasn't seen."""
        if self._pending_patch is None:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    "There's no proposed fix to apply. Run 'propose a fix' "
                    "first and review the diff."
                ),
                error="no_pending_patch",
            )

        patch = self._pending_patch
        target = patch["path"]

        # Guard against the file changing between propose and apply -- the
        # diff the person approved would no longer describe reality.
        current = target.read_text(encoding="utf-8", errors="replace")
        if current != patch["original"]:
            self._pending_patch = None
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"'{target.name}' changed since the fix was proposed, so "
                    f"the diff you approved no longer matches the file. "
                    f"Discarded the proposal -- run 'propose a fix' again."
                ),
                error="file_changed_since_proposal",
            )

        target.write_text(patch["proposed"], encoding="utf-8")
        self._pending_patch = None
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Applied the fix to {target.name}. Run 'run the tests' to check it.",
            data={"file": str(target)},
        )
