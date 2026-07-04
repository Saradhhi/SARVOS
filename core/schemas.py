"""
Core data contracts for SARVOS.

Every agent, the orchestrator, and the memory engine communicate exclusively
through these schemas. This is the "agent protocol" — the piece the SARVOS
spec left as a one-line gesture ("agents communicate using structured tasks
with shared context"). Concretely, that means:

- A Task is the unit of work handed to an agent.
- An AgentResult is what an agent must return — including whether it needs
  human confirmation before any effect lands.
- A ConversationTurn is what gets persisted to memory.

Keeping these as explicit Pydantic models (not free-form dicts) means the
orchestrator can validate agent output, log it, and reason about it — which
is required for the audit logging and rollback goals in the spec.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class AgentName(str, Enum):
    """Initial agent roster (Phase 1a). Extend as new agents are added."""

    PLANNER = "planner"
    CODING = "coding"
    MEMORY = "memory"
    GENERAL = "general"


class RiskLevel(str, Enum):
    """
    How much scrutiny a task's *effects* require.

    SAFE      - read-only / informational, no confirmation needed.
    SENSITIVE - writes local state SARVOS owns (e.g. saves a memory), logged
                but not blocked.
    DESTRUCTIVE - irreversible or external-effect actions (file deletion,
                  shell commands, sending messages). Requires explicit user
                  confirmation before execution, per the spec's security
                  model, unless the user has granted persistent permission
                  for that exact action type.
    """

    SAFE = "safe"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"


class TaskStatus(str, Enum):
    PENDING = "pending"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"  # user declined confirmation


class Task(BaseModel):
    """A single unit of work routed to exactly one agent."""

    task_id: str = Field(default_factory=lambda: _new_id("task"))
    parent_request_id: str  # ties this task back to the originating user turn
    agent: AgentName
    instruction: str  # natural-language instruction for the agent
    context: dict[str, Any] = Field(default_factory=dict)  # shared context slice
    risk: RiskLevel = RiskLevel.SAFE
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=_now)


class AgentResult(BaseModel):
    """What an agent must return after handling a Task."""

    task_id: str
    agent: AgentName
    success: bool
    output: str  # human-readable result
    data: dict[str, Any] = Field(default_factory=dict)  # structured payload
    needs_confirmation: bool = False
    confirmation_prompt: str | None = None
    new_tasks: list[Task] = Field(default_factory=list)  # follow-up work
    error: str | None = None


class ConversationTurn(BaseModel):
    """One exchange, persisted to episodic memory."""

    turn_id: str = Field(default_factory=lambda: _new_id("turn"))
    request_id: str = Field(default_factory=lambda: _new_id("req"))
    role: str  # "user" | "assistant" | "system"
    content: str
    agent: AgentName | None = None
    timestamp: datetime = Field(default_factory=_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecord(BaseModel):
    """A retrievable unit of semantic memory (fact, preference, note)."""

    record_id: str = Field(default_factory=lambda: _new_id("mem"))
    text: str
    kind: str = "note"  # "note" | "preference" | "fact" | "workflow"
    source_turn_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    tags: list[str] = Field(default_factory=list)
