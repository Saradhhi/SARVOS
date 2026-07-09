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

    def preflight(self, task: Task) -> AgentResult | None:
        """Optional READ-ONLY check, run BEFORE the confirmation gate.

        Return None to proceed, or a failed AgentResult to abandon the task
        without ever prompting the person.

        Exists because of a real annoyance found in live testing: asked to
        "close the notepad window" when no such window was open, SARVOS
        prompted "This looks destructive. Proceed? [y/n]", waited for a "y",
        and only then reported "No open window matching 'notepad'". The
        person was asked to authorize destroying something that didn't
        exist.

        This hook must NEVER perform side effects. It runs before
        confirmation precisely so that it can *decline* work -- it must not
        become a way to *do* work before the gate, which is the exact flaw
        this project rejected in an earlier third-party integration. The
        orchestrator gate remains the single choke point for anything that
        actually changes the world.
        """
        return None

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
