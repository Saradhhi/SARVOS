"""
Base Agent interface.

Every agent — Planner, Coding, Memory, and future ones (Research, Browser,
DevOps, Salesforce Specialist, ...) — implements this same contract. The
orchestrator never knows an agent's internals; it only ever calls `handle`
and gets back an AgentResult. This is what makes the agent roster
extensible: adding an agent means writing one class, not touching the
orchestrator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.schemas import AgentName, AgentResult, RiskLevel, Task
from memory.engine import MemoryEngine


class BaseAgent(ABC):
    name: AgentName

    def __init__(self, memory: MemoryEngine):
        self.memory = memory

    @abstractmethod
    def handle(self, task: Task) -> AgentResult:
        """Execute the task and return a structured result.

        Implementations must NOT perform destructive side effects if
        task.risk == RiskLevel.DESTRUCTIVE and the orchestrator hasn't
        marked the task as confirmed — check `task.context.get("confirmed")`
        before doing anything irreversible.
        """
        raise NotImplementedError

    def _blocked_pending_confirmation(self, task: Task, prompt: str) -> AgentResult:
        """Helper: use when a task needs human sign-off before proceeding."""
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            success=False,
            output="Waiting for user confirmation.",
            needs_confirmation=True,
            confirmation_prompt=prompt,
        )
