"""
SARVOS Phase 1a — CLI entry point.

Run: python main.py

This proves the core loop end to end: user input -> Planner -> routed agent
-> memory read/write -> confirmation gating on risky actions -> audit log.
No voice, no UI, no automation yet — those build on top of this without
changing the protocol underneath.

Try:
  remember that I prefer dark mode
  what do you know about my preferences
  debug this function
  delete all my files          <- triggers a confirmation prompt
"""

from __future__ import annotations

import uuid

from core.orchestrator import Orchestrator, PendingConfirmation
from core.schemas import AgentName
from memory.engine import MemoryEngine
from agents.planner import PlannerAgent
from agents.coding import CodingAgent
from agents.memory_agent import MemoryAgent
from agents.general import GeneralAgent


def build_orchestrator(db_path: str = "sarvos.db") -> Orchestrator:
    memory = MemoryEngine()
    agents = {
        AgentName.PLANNER: PlannerAgent(memory),
        AgentName.CODING: CodingAgent(memory),
        AgentName.MEMORY: MemoryAgent(memory),
        AgentName.GENERAL: GeneralAgent(memory),
    }
    return Orchestrator(memory, agents)


def main() -> None:
    orchestrator = build_orchestrator()
    print("SARVOS Phase 1a — type 'exit' to quit, 'log' to see the audit trail.\n")

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text:
            continue
        if text.lower() in ("exit", "quit"):
            break
        if text.lower() == "log":
            for entry in orchestrator.memory.store.recent_audit_log(10):
                print(f"  [{entry['timestamp']}] {entry['action']} "
                      f"agent={entry['agent']} risk={entry['risk']} {entry['detail']}")
            continue

        request_id = str(uuid.uuid4())
        try:
            results = orchestrator.handle_user_message(text, request_id)
        except PendingConfirmation as pending:
            print(f"sarvos> {pending.prompt} [y/n]")
            answer = input("you> ").strip().lower()
            approved = answer in ("y", "yes")
            results = orchestrator.resume_with_confirmation(
                pending.task, approved, request_id
            )

        for result in results:
            if result.output and not result.new_tasks:
                print(f"sarvos> {result.output}")


if __name__ == "__main__":
    main()
