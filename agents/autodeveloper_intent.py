"""
Parses AutoDeveloper instructions into a structured intent. Same
deterministic-classification pattern as every other agent here.

REAL BUG FIXED FROM THE ORIGINAL INTEGRATION: the original routing was
`if 'develop' in text.lower()`, which would incorrectly match completely
ordinary sentences like "let's develop this idea further" or "I want to
develop my skills" -- neither has anything to do with running tests or
deploying code. Fixed with specific phrase patterns, each covered by an
explicit negative-case test.

Risk levels, deliberately conservative: RUN_TESTS and DEPLOY are both
DESTRUCTIVE (they execute real subprocesses with real side effects --
even "running tests" can have side effects depending on what the test
suite does), gated behind the orchestrator's real confirmation check
BEFORE anything runs. This is the actual fix for the original integration's
core flaw: it executed first and showed a "confirmation" message
afterward, which didn't gate anything at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    ANALYZE = "analyze"
    RUN_TESTS = "run_tests"
    DEPLOY = "deploy"
    UNKNOWN = "unknown"


@dataclass
class AutoDeveloperIntent:
    operation: Operation
    risk: RiskLevel
    raw_instruction: str = ""


_ANALYZE_RE = re.compile(
    r"\banalyze (?:the )?workspace\b|\banalyze (?:the )?project\b|"
    r"\bautodeveloper analyze\b|\bscan (?:the )?workspace\b",
    re.I,
)
_RUN_TESTS_RE = re.compile(
    r"\brun (?:the )?tests?\b|\brun (?:the )?test suite\b|"
    r"\bautodeveloper (?:run )?tests?\b",
    re.I,
)
_DEPLOY_RE = re.compile(
    r"\bdeploy (?:the )?(?:project|app|application|code)\b|\bautodeveloper deploy\b|"
    r"^deploy$",
    re.I,
)


def classify(instruction: str) -> AutoDeveloperIntent:
    text = instruction.strip()

    if _RUN_TESTS_RE.search(text):
        return AutoDeveloperIntent(operation=Operation.RUN_TESTS, risk=RiskLevel.DESTRUCTIVE, raw_instruction=instruction)
    if _DEPLOY_RE.search(text):
        return AutoDeveloperIntent(operation=Operation.DEPLOY, risk=RiskLevel.DESTRUCTIVE, raw_instruction=instruction)
    if _ANALYZE_RE.search(text):
        return AutoDeveloperIntent(operation=Operation.ANALYZE, risk=RiskLevel.SAFE, raw_instruction=instruction)

    return AutoDeveloperIntent(operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction)


def looks_like_autodeveloper_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
