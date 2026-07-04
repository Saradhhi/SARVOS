"""
Coding Agent.

Phase 1a scope: proves routing works for coding-flavored requests. It does
NOT yet execute real code generation via an LLM or touch the
filesystem/terminal — that's Phase 3 (Automation) territory, where Tool
Runtime integrations (git, terminal, VS Code) land. Wiring an actual
code-gen model in is a drop-in replacement for `_draft_response` once an LLM
client is configured; nothing about the orchestrator or protocol changes.

Note on confirmation: destructive-risk gating happens centrally in the
Orchestrator before this agent is ever dispatched (see core/orchestrator.py).
By the time `handle` runs, either the task was never risky or the user has
already approved it — this agent doesn't need to (and shouldn't have to)
re-implement that check.
"""

from __future__ import annotations

from core.schemas import AgentName, AgentResult, Task
from agents.base import BaseAgent


class CodingAgent(BaseAgent):
    name = AgentName.CODING

    def handle(self, task: Task) -> AgentResult:
        response = self._draft_response(task.instruction)
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            success=True,
            output=response,
        )

    def _draft_response(self, instruction: str) -> str:
        # Placeholder for real code generation (LLM call). Explicitly marked
        # per the spec's "no placeholder implementations unless requested" —
        # this one IS requested, since wiring a live model is out of scope
        # for the foundation build.
        return (
            "[coding-agent stub] I'd generate/debug code for: "
            f"'{instruction}'. Real code generation isn't wired up yet — "
            "this agent currently only proves the routing and confirmation "
            "flow works end to end."
        )
