"""
Parses system-information instructions into a structured intent. Same
deterministic-classification pattern as automation/browser/research_intent.py.

All operations here are read-only (querying CPU/RAM/disk/battery/network
stats) -- risk is always SAFE, no confirmation gating needed at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    CPU = "cpu"
    RAM = "ram"
    DISK = "disk"
    BATTERY = "battery"
    NETWORK = "network"
    ALL = "all"
    UNKNOWN = "unknown"


@dataclass
class SystemInfoIntent:
    operation: Operation
    risk: RiskLevel
    raw_instruction: str = ""


# Order matters: more specific checks (cpu/ram/disk/battery/network) are
# checked before the general "system info"/"how's my computer" catch-all,
# so "check my cpu usage" routes to CPU specifically, not ALL.
_PATTERNS = [
    (Operation.CPU, re.compile(r"\bcpu\b|\bprocessor\b", re.I)),
    (Operation.RAM, re.compile(r"\bram\b|\bmemory\b", re.I)),
    (Operation.DISK, re.compile(r"\bdisk\b|\bstorage\b|\bhard drive\b|\bhdd\b|\bssd\b", re.I)),
    (Operation.BATTERY, re.compile(r"\bbattery\b", re.I)),
    (Operation.NETWORK, re.compile(r"\bnetwork\b|\bwifi\b|\binternet connection\b", re.I)),
]

_GENERAL_RE = re.compile(
    r"\bsystem (?:info|information|status|stats)\b|"
    r"how(?:'s| is) (?:my|the) (?:computer|system|machine|pc|laptop)\b|"
    r"\bcomputer status\b",
    re.I,
)

_TRIGGER_WORDS = ("check", "what's", "what is", "show", "how much", "how's", "how is")


def classify(instruction: str) -> SystemInfoIntent:
    text = instruction.strip()

    if _GENERAL_RE.search(text):
        return SystemInfoIntent(operation=Operation.ALL, risk=RiskLevel.SAFE, raw_instruction=instruction)

    # Specific resource mentioned (cpu/ram/disk/battery/network) -- but
    # only treat it as a system-info request if it also looks like a
    # QUERY (contains a trigger word), so unrelated sentences that happen
    # to mention these words aren't misrouted here.
    lowered = text.lower()
    has_trigger = any(t in lowered for t in _TRIGGER_WORDS) or lowered.startswith(
        ("cpu", "ram", "memory", "disk", "battery", "network")
    )
    if has_trigger:
        for operation, pattern in _PATTERNS:
            if pattern.search(text):
                return SystemInfoIntent(operation=operation, risk=RiskLevel.SAFE, raw_instruction=instruction)

    return SystemInfoIntent(operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction)


def looks_like_system_info_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
