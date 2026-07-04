import tempfile
from pathlib import Path

import pytest

from core.orchestrator import Orchestrator, PendingConfirmation
from core.schemas import AgentName
from memory.engine import MemoryEngine
from memory.store import Store
from agents.planner import PlannerAgent
from agents.coding import CodingAgent
from agents.memory_agent import MemoryAgent
from agents.general import GeneralAgent


@pytest.fixture
def orchestrator() -> Orchestrator:
    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryEngine(store=Store(Path(tmp) / "test.db"))
        agents = {
            AgentName.PLANNER: PlannerAgent(memory),
            AgentName.CODING: CodingAgent(memory),
            AgentName.MEMORY: MemoryAgent(memory),
            AgentName.GENERAL: GeneralAgent(memory),
        }
        yield Orchestrator(memory, agents)


def test_memory_routing(orchestrator: Orchestrator):
    results = orchestrator.handle_user_message(
        "remember that I like tea", request_id="req1"
    )
    assert any(r.agent == AgentName.MEMORY and r.success for r in results)


def test_general_fallback_routing(orchestrator: Orchestrator):
    results = orchestrator.handle_user_message(
        "tell me a fun fact", request_id="req2"
    )
    assert any(r.agent == AgentName.GENERAL for r in results)


def test_safe_coding_task_does_not_require_confirmation(orchestrator: Orchestrator):
    results = orchestrator.handle_user_message(
        "debug this function for me", request_id="req3"
    )
    assert any(r.agent == AgentName.CODING and r.success for r in results)


def test_destructive_task_raises_pending_confirmation(orchestrator: Orchestrator):
    with pytest.raises(PendingConfirmation) as exc_info:
        orchestrator.handle_user_message(
            "delete all my code files", request_id="req4"
        )
    assert exc_info.value.task.agent == AgentName.CODING


def test_confirmation_approved_proceeds(orchestrator: Orchestrator):
    with pytest.raises(PendingConfirmation) as exc_info:
        orchestrator.handle_user_message("delete everything", request_id="req5")
    pending_task = exc_info.value.task

    results = orchestrator.resume_with_confirmation(
        pending_task, approved=True, request_id="req5"
    )
    assert any(r.success for r in results)


def test_confirmation_rejected_blocks_action(orchestrator: Orchestrator):
    with pytest.raises(PendingConfirmation) as exc_info:
        orchestrator.handle_user_message("delete everything", request_id="req6")
    pending_task = exc_info.value.task

    results = orchestrator.resume_with_confirmation(
        pending_task, approved=False, request_id="req6"
    )
    assert all(not r.success for r in results)
    assert "won't do that" in results[0].output.lower()


def test_audit_log_records_dispatch_and_confirmation(orchestrator: Orchestrator):
    with pytest.raises(PendingConfirmation) as exc_info:
        orchestrator.handle_user_message("delete stuff", request_id="req7")
    pending_task = exc_info.value.task
    orchestrator.resume_with_confirmation(pending_task, approved=False, request_id="req7")

    log = orchestrator.memory.store.recent_audit_log(20)
    actions = [entry["action"] for entry in log]
    assert "dispatch" in actions
    assert "confirmation_decision" in actions
