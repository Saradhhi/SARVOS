"""
Parses terminal/shell-flavored instructions into a structured intent.

IMPORTANT SCOPE NOTE: this is deliberately NOT "run any command the user
describes." Arbitrary shell/PowerShell execution driven by natural
language is a fundamentally different risk category than the other
agents' allowlists (git subcommands, browser navigation, search
queries) -- there's no realistic way to allowlist "anything a user might
phrase as a command." Instead, this covers a fixed, small set of real
diagnostics (running processes, current user, hostname, OS version),
each backed by a direct Python library call in agents/terminal.py
(psutil/getpass/socket/platform) rather than a subprocess shell-out --
strictly safer AND more reliable/cross-platform than parsing
tasklist/whoami/hostname/ver output would be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    PROCESSES = "processes"
    CURRENT_USER = "current_user"
    HOSTNAME = "hostname"
    OS_VERSION = "os_version"
    UNKNOWN = "unknown"


@dataclass
class TerminalIntent:
    operation: Operation
    risk: RiskLevel
    raw_instruction: str = ""


_PATTERNS = [
    (Operation.PROCESSES, re.compile(
        r"\brunning processes\b|\blist processes\b|\bwhat'?s running\b|"
        r"\bshow (?:me )?(?:the )?processes\b|\btask list\b|\btasklist\b",
        re.I,
    )),
    (Operation.CURRENT_USER, re.compile(
        r"\bwho am i\b|\bwhoami\b|\bcurrent user\b|\blogged in as\b", re.I,
    )),
    (Operation.HOSTNAME, re.compile(
        r"\bhostname\b|\bcomputer name\b|\bmachine name\b|\bpc name\b", re.I,
    )),
    (Operation.OS_VERSION, re.compile(
        r"\bos version\b|\boperating system version\b|\bwindows version\b|"
        r"\bwhat os\b|\bwhat operating system\b",
        re.I,
    )),
]


def classify(instruction: str) -> TerminalIntent:
    text = instruction.strip()
    for operation, pattern in _PATTERNS:
        if pattern.search(text):
            return TerminalIntent(operation=operation, risk=RiskLevel.SAFE, raw_instruction=instruction)
    return TerminalIntent(operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction)


def looks_like_terminal_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
