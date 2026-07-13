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

import re

import uuid

from agents.planner import routes_to_a_specialist
from core.orchestrator import Orchestrator, PendingConfirmation
from core.factory import create_orchestrator


def build_orchestrator(db_path: str = "sarvos.db") -> Orchestrator:
    return create_orchestrator(db_path)


STRAY_CONFIRMATION_REPLY = (
    "Nothing is waiting for confirmation right now, so there's nothing to "
    "say yes or no to. If you meant to apply a proposed fix, the command "
    "is: apply the fix"
)


SHELL_COMMAND_REPLY = (
    "That looks like a shell command. SARVOS isn't a shell -- type it in your "
    "terminal instead. If you meant to use SARVOS, try 'read <file>', 'run "
    "the tests', 'list documents', or just ask a question."
)

# Commands people reflexively type at any prompt. Anchored to the START of
# the input and requiring the whole line to BE the command, so that real
# questions ("how do I run python main.py?") still reach the assistant.
_SHELL_COMMAND_RE = re.compile(
    r"^(?:python3?|pip3?|py|node|npm|npx|git|cd|ls|dir|cat|type|echo|"
    r"mkdir|rm|del|cp|copy|mv|move|pytest|ollama|curl|wget|clear|cls)"
    r"(?:\s+\S+)*\s*$",
    re.I,
)


# These are ONLY ever commands. Nobody asks SARVOS a question whose entire
# text is "dir". Contrast bare "python" or "git", which are far more likely
# to be someone naming a topic than issuing a command.
_BARE_COMMANDS = {"dir", "ls", "cls", "clear", "pwd"}


def looks_like_shell_syntax(text: str) -> bool:
    """Shape check only: does this input READ like a shell command?

    NOT sufficient on its own. Several real SARVOS commands begin with the
    same verbs -- 'type "x" into the name field' (browser), 'move notepad to
    100, 200' (windows), 'git status of my repo' (automation). A verb-prefix
    match cannot tell them apart, and this function wrongly matched all three
    before its own test caught it.

    Callers must therefore only treat a positive result as meaningful for
    input that NO agent recognized -- see is_shell_command.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.lower() in _BARE_COMMANDS:
        return True
    if " " not in stripped and "." not in stripped:
        # A bare word like "git" or "python" is more likely a topic than a
        # command -- someone asking about Python, not running it.
        return False
    return bool(_SHELL_COMMAND_RE.match(stripped))


def is_shell_command(text: str, recognized_by_an_agent: bool) -> bool:
    """True if the input is a stray shell command that must not reach the LLM.

    Both fabrications seen in live use came through exactly this door:

    - 'type calc.py' produced a complete, invented "Before / After" listing
      of a file the model had never read, stating it "has been updated".
    - 'python main.py' produced "I've run the command, but I still can't find
      any information about a file named resume.pdf" -- it ran nothing, and
      invented both the file and the premise.

    A chat model handed a shell command will try to make conversational sense
    of it, and conversational sense means inventing a context.

    The `recognized_by_an_agent` guard is essential, not decorative: if the
    planner routes the input somewhere real, it is a SARVOS command that
    merely shares a verb with a shell one, and must be left alone.
    """
    if recognized_by_an_agent:
        return False
    return looks_like_shell_syntax(text)


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

        # Shell commands typed here must not reach the chat LLM -- it will
        # invent a context for them. Only fires when no specialist agent
        # claims the input, since 'type "x" into the name field' is a real
        # browser command that merely starts with a shell verb.
        if is_shell_command(text, routes_to_a_specialist(text, orchestrator.memory)):
            print(f"sarvos> {SHELL_COMMAND_REPLY}")
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
