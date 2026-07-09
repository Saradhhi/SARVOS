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

AUTO-HEAL, rebuilt properly as TWO separate operations (the original had
a `simulate_llm_patch` stub returning a hardcoded fake test, written to
disk automatically before any confirmation -- all three of those things
are deliberately inverted here):
- PROPOSE_FIX is SAFE: it runs the real test command, sends the REAL
  failure output and REAL file contents to the LLM, and shows you a real
  unified diff. It writes NOTHING.
- APPLY_FIX is DESTRUCTIVE: it writes the already-proposed patch to disk,
  gated by the orchestrator's real confirmation check. It can only run
  AFTER a proposal exists -- there is no automatic heal loop.
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
    PROPOSE_FIX = "propose_fix"
    APPLY_FIX = "apply_fix"
    UNKNOWN = "unknown"


@dataclass
class AutoDeveloperIntent:
    operation: Operation
    risk: RiskLevel
    target_file: str | None = None
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
_PROPOSE_FIX_RE = re.compile(
    r"\bpropose (?:a )?(?:fix|patch)\b|\bsuggest (?:a )?(?:fix|patch)\b|"
    r"\bwhat would fix (?:the )?tests?\b|\bautodeveloper propose\b",
    re.I,
)
# Optional "... for <file>.py" suffix. When absent, the agent RECOMMENDS a
# file rather than picking one silently -- the person must then name it
# explicitly. See _propose_fix in autodeveloper.py for why: test output
# often doesn't reveal which SOURCE file is actually buggy, so choosing one
# automatically risks overwriting the wrong file.
_TARGET_FILE_RE = re.compile(r"\bfor\s+([\w./\\-]+\.py)\b", re.I)
_APPLY_FIX_RE = re.compile(
    r"\bapply (?:the )?(?:fix|patch)\b|\bwrite (?:the )?(?:fix|patch)\b|"
    r"\bautodeveloper apply\b",
    re.I,
)


def classify(instruction: str) -> AutoDeveloperIntent:
    text = instruction.strip()

    # Checked BEFORE run_tests/deploy: "propose a fix" and "apply the fix"
    # are more specific, and must never be swallowed by a looser pattern.
    if _PROPOSE_FIX_RE.search(text):
        m = _TARGET_FILE_RE.search(text)
        return AutoDeveloperIntent(
            operation=Operation.PROPOSE_FIX, risk=RiskLevel.SAFE,
            target_file=m.group(1) if m else None, raw_instruction=instruction,
        )
    if _APPLY_FIX_RE.search(text):
        return AutoDeveloperIntent(operation=Operation.APPLY_FIX, risk=RiskLevel.DESTRUCTIVE, raw_instruction=instruction)

    if _RUN_TESTS_RE.search(text):
        return AutoDeveloperIntent(operation=Operation.RUN_TESTS, risk=RiskLevel.DESTRUCTIVE, raw_instruction=instruction)
    if _DEPLOY_RE.search(text):
        return AutoDeveloperIntent(operation=Operation.DEPLOY, risk=RiskLevel.DESTRUCTIVE, raw_instruction=instruction)
    if _ANALYZE_RE.search(text):
        return AutoDeveloperIntent(operation=Operation.ANALYZE, risk=RiskLevel.SAFE, raw_instruction=instruction)

    return AutoDeveloperIntent(operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction)


def looks_like_autodeveloper_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
