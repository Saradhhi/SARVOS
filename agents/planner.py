"""
Executive Planner agent.

Implements the spec's lifecycle: Understand -> Clarify -> Plan -> Estimate
-> Identify tools -> Execute -> Verify -> Summarize -> Store.

For Phase 1a this is a rule-based decomposer (keyword/heuristic routing),
NOT an LLM-driven planner. That's an intentional, explicit scope cut: wiring
this to an actual LLM call is a follow-up (drop-in replacement of
`_decompose`), and shipping the honest rule-based version now means the
orchestrator/agent-protocol plumbing gets tested end-to-end before adding
LLM cost and latency on top of it.
"""

from __future__ import annotations

from core.schemas import AgentName, AgentResult, RiskLevel, Task
from agents.base import BaseAgent

DESTRUCTIVE_KEYWORDS = ("delete", "remove", "drop", "wipe", "format", "rm ")
CODE_KEYWORDS = ("code", "function", "bug", "debug", "refactor", "script", "class ")
MEMORY_KEYWORDS = ("remember", "recall", "what did i", "forget", "my preference")


class PlannerAgent(BaseAgent):
    name = AgentName.PLANNER

    def handle(self, task: Task) -> AgentResult:
        subtasks = self._decompose(task)
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            success=True,
            output=f"Decomposed into {len(subtasks)} subtask(s).",
            new_tasks=subtasks,
        )

    def _decompose(self, task: Task) -> list[Task]:
        text = task.instruction.lower()

        # Destructive-intent detection runs FIRST and independently of which
        # agent ends up handling the task. This matters: "delete everything"
        # has no coding keyword in it, but it's still destructive. Risk is a
        # property of the instruction's *effect*, not of which specialty
        # handles it — so it can't be scoped to just one category's branch.
        risk = (
            RiskLevel.DESTRUCTIVE
            if any(k in text for k in DESTRUCTIVE_KEYWORDS)
            else RiskLevel.SAFE
        )

        if any(k in text for k in MEMORY_KEYWORDS):
            agent = AgentName.MEMORY
        elif any(k in text for k in CODE_KEYWORDS):
            agent = AgentName.CODING
        else:
            agent = AgentName.GENERAL

        return [
            Task(
                parent_request_id=task.parent_request_id,
                agent=agent,
                instruction=task.instruction,
                context=task.context,
                risk=risk,
            )
        ]
