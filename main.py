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
from core.factory import create_orchestrator


def build_orchestrator(db_path: str = "sarvos.db") -> Orchestrator:
    return create_orchestrator(db_path)


STRAY_CONFIRMATION_REPLY = (
    "Nothing is waiting for confirmation right now, so there's nothing to "
    "say yes or no to. If you meant to apply a proposed fix, the command "
    "is: apply the fix"
)


def is_stray_confirmation(text: str) -> bool:
    """True if the input is a bare yes/no answer with nothing pending.

    Such input must NEVER reach the chat LLM. Found from real use: after
    'propose a fix' (a SAFE operation that asks nothing), a stray 'y' fell
    through to the general agent, which improvised confident prose claiming
    it had applied the patch. Nothing had been written. An affirmative with
    nothing to affirm is a user mistake, not a question to answer."""
    return text.strip().lower() in {"y", "yes", "n", "no"}


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

        # A bare yes/no with nothing pending must NOT reach the chat LLM.
        if is_stray_confirmation(text):
            print(f"sarvos> {STRAY_CONFIRMATION_REPLY}")
            continue

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
