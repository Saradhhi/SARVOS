"""
Parses automation-flavored instructions into a structured intent, and
classifies the risk level each intent carries. This is intentionally NOT an
LLM freely deciding what shell commands to run -- that would be a serious,
hard-to-bound security risk for a feature with real filesystem/subprocess
effects. Instead, every operation SARVOS can actually perform is an
explicit, enumerated case here, matched via deterministic pattern parsing.
If it doesn't match a known pattern, SARVOS says so rather than guessing.

This module is shared by agents/planner.py (which needs to know the RISK
level to set on the Task, before the orchestrator's central confirmation
gate ever runs) and agents/automation.py (which needs to know exactly what
to DO once a task is dispatched, after any required confirmation). Keeping
classification and parsing in one place means they can't drift out of sync
with each other -- there's no world where the Planner thinks something is
SAFE but the AutomationAgent treats it as DESTRUCTIVE, or vice versa.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    READ_FILE = "read_file"
    LIST_DIR = "list_dir"
    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    MOVE_FILE = "move_file"
    COPY_FILE = "copy_file"
    GIT_COMMAND = "git_command"
    SHELL_COMMAND = "shell_command"
    UNKNOWN = "unknown"


@dataclass
class AutomationIntent:
    operation: Operation
    risk: RiskLevel
    # Operation-specific parameters, all optional depending on operation:
    path: str | None = None
    dest_path: str | None = None  # for move/copy
    content: str | None = None
    git_args: list[str] | None = None
    shell_args: list[str] | None = None
    raw_instruction: str = ""


# Git subcommands are explicitly categorized -- nothing not in one of these
# sets is runnable at all (see AutomationAgent._run_git). This is a
# deliberate allowlist, not a denylist: an unrecognized git subcommand is
# refused, not passed through.
GIT_SAFE_SUBCOMMANDS = {"status", "log", "diff", "branch", "show", "remote"}
GIT_SENSITIVE_SUBCOMMANDS = {"add", "commit", "fetch", "stash"}
GIT_DESTRUCTIVE_SUBCOMMANDS = {"push", "pull", "checkout", "reset", "merge", "rebase"}

# Real, standalone, read-only executables -- deliberately NOT shell
# built-ins (Windows' `dir`, `echo`, `cd`, `set` etc. aren't real .exe
# files and need cmd.exe involved to run at all, which reopens the door
# to shell interpretation we're specifically avoiding here). Every entry
# below is a genuine program on disk, on both Windows and Linux/Mac, so
# subprocess.run([...], shell=False) can find and execute it directly.
# This is an allowlist, not a denylist -- anything not listed here is
# DESTRUCTIVE (gated behind confirmation), not silently permitted.
SAFE_SHELL_COMMANDS = {
    "whoami", "hostname", "systeminfo", "tasklist", "ps", "ipconfig",
    "ifconfig", "where", "which",
}

_READ_FILE_RE = re.compile(r"^(?:read|show|open|cat|display)\s+(?:the\s+)?file\s+(.+)$", re.I)
_LIST_DIR_RE = re.compile(
    r"^(?:list|show)\s+(?:the\s+)?(?:files?|contents?)\s+(?:in|of)\s+(.+)$", re.I
)
_WRITE_FILE_RE = re.compile(
    r"^(?:write|create|save)\s+(?:a\s+)?file\s+(?:called\s+|named\s+)?"
    r"(\S+)\s*(?:with|containing)?\s*[:\-]?\s*(.*)$",
    re.I,
)
_DELETE_FILE_RE = re.compile(r"^delete\s+(?:the\s+)?file\s+(.+)$", re.I)
_MOVE_FILE_RE = re.compile(
    r"^(?:move|rename)\s+(?:the\s+)?file\s+(\S+)\s+to\s+(\S+)$", re.I
)
_COPY_FILE_RE = re.compile(r"^copy\s+(?:the\s+)?file\s+(\S+)\s+to\s+(\S+)$", re.I)
_GIT_RE = re.compile(r"^git\s+(\S+)(?:\s+(.*))?$", re.I)
_SHELL_COMMAND_RE = re.compile(
    r"^(?:run|execute)\s+(?:the\s+)?(?:shell\s+command|terminal\s+command|command)?\s*:?\s*(.+)$",
    re.I,
)


def classify(instruction: str) -> AutomationIntent:
    """Pure text -> AutomationIntent classification. No filesystem or
    subprocess access happens here -- this only decides WHAT something
    means and how risky it is, never performs it."""
    text = instruction.strip()

    match = _GIT_RE.match(text)
    if match:
        subcommand = match.group(1).lower()
        rest = match.group(2) or ""
        args = [subcommand] + (rest.split() if rest else [])
        if subcommand in GIT_SAFE_SUBCOMMANDS:
            risk = RiskLevel.SAFE
        elif subcommand in GIT_SENSITIVE_SUBCOMMANDS:
            risk = RiskLevel.SENSITIVE
        elif subcommand in GIT_DESTRUCTIVE_SUBCOMMANDS:
            risk = RiskLevel.DESTRUCTIVE
        else:
            # Unrecognized subcommand -- refuse rather than guess. Treated
            # as DESTRUCTIVE so it's gated behind confirmation; the agent
            # itself will refuse to actually run it regardless (allowlist,
            # not a denylist -- see GIT_SAFE/SENSITIVE/DESTRUCTIVE sets).
            risk = RiskLevel.DESTRUCTIVE
        return AutomationIntent(
            operation=Operation.GIT_COMMAND, risk=risk, git_args=args,
            raw_instruction=instruction,
        )

    match = _READ_FILE_RE.match(text)
    if match:
        return AutomationIntent(
            operation=Operation.READ_FILE, risk=RiskLevel.SAFE,
            path=match.group(1).strip(), raw_instruction=instruction,
        )

    match = _LIST_DIR_RE.match(text)
    if match:
        return AutomationIntent(
            operation=Operation.LIST_DIR, risk=RiskLevel.SAFE,
            path=match.group(1).strip(), raw_instruction=instruction,
        )

    match = _DELETE_FILE_RE.match(text)
    if match:
        return AutomationIntent(
            operation=Operation.DELETE_FILE, risk=RiskLevel.DESTRUCTIVE,
            path=match.group(1).strip(), raw_instruction=instruction,
        )

    match = _MOVE_FILE_RE.match(text)
    if match:
        # DESTRUCTIVE: the source file is removed from its original
        # location, and (checked at execution time, not here -- classify()
        # deliberately does no filesystem access) an existing file at the
        # destination would be overwritten if allowed to proceed silently.
        return AutomationIntent(
            operation=Operation.MOVE_FILE, risk=RiskLevel.DESTRUCTIVE,
            path=match.group(1).strip(), dest_path=match.group(2).strip(),
            raw_instruction=instruction,
        )

    match = _COPY_FILE_RE.match(text)
    if match:
        # SENSITIVE, not DESTRUCTIVE: unlike move, the source file is left
        # intact -- worst case if something goes wrong is an unwanted
        # extra file, not data loss of the original.
        return AutomationIntent(
            operation=Operation.COPY_FILE, risk=RiskLevel.SENSITIVE,
            path=match.group(1).strip(), dest_path=match.group(2).strip(),
            raw_instruction=instruction,
        )

    match = _WRITE_FILE_RE.match(text)
    if match:
        return AutomationIntent(
            operation=Operation.WRITE_FILE, risk=RiskLevel.SENSITIVE,
            path=match.group(1).strip(), content=match.group(2).strip(),
            raw_instruction=instruction,
        )

    match = _SHELL_COMMAND_RE.match(text)
    if match:
        command_text = match.group(1).strip()
        if not command_text:
            return AutomationIntent(
                operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction,
            )

        # "run git status" and "git status" must classify identically --
        # delegate to the same git logic above rather than treating git
        # through the coarser generic-shell-command allowlist, which would
        # otherwise gate a genuinely safe git subcommand behind
        # confirmation just because it arrived via "run ...".
        if command_text.lower().startswith("git "):
            return classify(command_text)

        try:
            tokens = shlex.split(command_text)
        except ValueError:
            # Unbalanced quotes or similar -- can't safely tokenize this,
            # so refuse rather than guess at what was meant.
            return AutomationIntent(
                operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction,
            )
        if not tokens:
            return AutomationIntent(
                operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction,
            )

        base_command = tokens[0].lower()
        risk = RiskLevel.SAFE if base_command in SAFE_SHELL_COMMANDS else RiskLevel.DESTRUCTIVE
        return AutomationIntent(
            operation=Operation.SHELL_COMMAND, risk=risk, shell_args=tokens,
            raw_instruction=instruction,
        )

    return AutomationIntent(
        operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction,
    )


def looks_like_automation_request(instruction: str) -> bool:
    """Cheap pre-check used by the Planner to decide whether to route to
    the Automation agent at all, before full classify() parsing."""
    return classify(instruction).operation != Operation.UNKNOWN
