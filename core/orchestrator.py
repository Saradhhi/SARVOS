"""
Agent Orchestrator — Layer 3 from the SARVOS spec.

This is the concrete answer to the question the spec left open: HOW do
agents communicate and get routed? The design here:

1. A user request becomes a Task for the PlannerAgent.
2. The Planner returns zero or more follow-up Tasks (AgentResult.new_tasks).
3. The orchestrator runs a task queue: pop a task, dispatch it to its named
   agent, handle the result (recurse on new_tasks, or surface a confirmation
   request to the caller and pause).
4. Every dispatch and every confirmation decision is written to the audit
   log — this is what "every important action should be observable" means
   concretely, not just as a principle.

This is deliberately a synchronous, single-threaded queue for Phase 1a.
Concurrent agent execution (needed once Research/Browser/DevOps agents can
run in parallel) is a Phase 2 change to `run` — the Task/AgentResult
contract doesn't need to change to support it, only the loop below does.
"""

from __future__ import annotations

from collections import deque

from core.schemas import (
    AgentName,
    AgentResult,
    ConversationTurn,
    RiskLevel,
    Task,
    TaskStatus,
)
from memory.engine import MemoryEngine
from agents.base import BaseAgent


class PendingConfirmation(Exception):
    """Raised (and caught by the caller, e.g. the CLI) when a task needs
    explicit user sign-off before the orchestrator can continue."""

    def __init__(self, task: Task, prompt: str):
        self.task = task
        self.prompt = prompt
        super().__init__(prompt)


class Orchestrator:
    def __init__(self, memory: MemoryEngine, agents: dict[AgentName, BaseAgent]):
        self.memory = memory
        self.agents = agents

    def handle_user_message(self, text: str, request_id: str) -> list[AgentResult]:
        """Entry point for a new user turn. Always starts at the Planner."""
        turn = ConversationTurn(request_id=request_id, role="user", content=text)
        self.memory.record_turn(turn)

        root_task = Task(
            parent_request_id=request_id,
            agent=AgentName.PLANNER,
            instruction=text,
        )
        return self._run_queue(deque([root_task]), request_id)

    def resume_with_confirmation(
        self, task: Task, approved: bool, request_id: str
    ) -> list[AgentResult]:
        """Called after the user answers a confirmation prompt."""
        self.memory.store.log_action(
            action="confirmation_decision",
            task_id=task.task_id,
            agent=task.agent.value,
            risk=task.risk.value,
            detail=f"approved={approved}",
        )
        if not approved:
            task.status = TaskStatus.REJECTED
            return [
                AgentResult(
                    task_id=task.task_id,
                    agent=task.agent,
                    success=False,
                    output="Okay, I won't do that.",
                )
            ]
        task.context["confirmed"] = True
        return self._run_queue(deque([task]), request_id)

    def _run_queue(self, queue: deque[Task], request_id: str) -> list[AgentResult]:
        results: list[AgentResult] = []
        while queue:
            task = queue.popleft()
            agent = self.agents.get(task.agent)
            if agent is None:
                results.append(
                    AgentResult(
                        task_id=task.task_id,
                        agent=task.agent,
                        success=False,
                        output=f"No agent registered for '{task.agent}'.",
                        error="unregistered_agent",
                    )
                )
                continue

            self.memory.store.log_action(
                action="dispatch",
                task_id=task.task_id,
                agent=task.agent.value,
                risk=task.risk.value,
                detail=task.instruction[:200],
            )

            # Confirmation gating lives HERE, not inside individual agents.
            # An agent forgetting to check task.risk must never be a way to
            # bypass confirmation — this is the single choke point the
            # spec's "explicit confirmation for destructive actions"
            # guarantee actually depends on. Agents may still add their own
            # narrower checks as defense-in-depth, but this is the backstop.
            if task.risk == RiskLevel.DESTRUCTIVE and not task.context.get("confirmed"):
                task.status = TaskStatus.AWAITING_CONFIRMATION
                prompt = (
                    f'This looks destructive/irreversible: "{task.instruction}". '
                    "Proceed?"
                )
                raise PendingConfirmation(task, prompt)

            task.status = TaskStatus.IN_PROGRESS
            result = agent.handle(task)
            results.append(result)

            if result.needs_confirmation:
                task.status = TaskStatus.AWAITING_CONFIRMATION
                # An agent can still request confirmation for a risk it
                # detects itself, even on a task the orchestrator marked SAFE
                # (e.g. it noticed something destructive on closer reading).
                raise PendingConfirmation(task, result.confirmation_prompt or "Proceed?")

            task.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED

            for followup in result.new_tasks:
                queue.append(followup)

            # Persist non-planner, terminal output as an assistant turn.
            if task.agent != AgentName.PLANNER and not result.new_tasks:
                self.memory.record_turn(
                    ConversationTurn(
                        request_id=request_id,
                        role="assistant",
                        content=result.output,
                        agent=task.agent,
                    )
                )

        return results
