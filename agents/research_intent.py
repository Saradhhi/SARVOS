"""
Parses research-flavored instructions into a structured intent. Same
pattern as agents/automation_intent.py and agents/browser_intent.py:
deterministic parsing, shared between the Planner (risk) and the
ResearchAgent (execution).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    SEARCH = "search"
    UNKNOWN = "unknown"


@dataclass
class ResearchIntent:
    operation: Operation
    risk: RiskLevel
    query: str | None = None
    raw_instruction: str = ""


_SEARCH_RE = re.compile(
    r"^(?:research|search(?:\s+for)?|look\s?up|find\s+(?:information|out)\s+"
    r"(?:on|about))\s+(.+)$",
    re.I,
)


def classify(instruction: str) -> ResearchIntent:
    text = instruction.strip()
    match = _SEARCH_RE.match(text)
    if match:
        query = match.group(1).strip().rstrip("?.!")
        # Guards against a regex backtracking edge case: "search for" with
        # nothing after it can match with the verb backing off to just
        # "search", leaving "for" itself captured as the "query" -- which
        # isn't a real query at all, just a stray connector word.
        if query and query.lower() not in {"for", "about", "on"}:
            return ResearchIntent(
                operation=Operation.SEARCH, risk=RiskLevel.SAFE,
                query=query, raw_instruction=instruction,
            )
    return ResearchIntent(
        operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction,
    )


def looks_like_research_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
