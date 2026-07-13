"""
Parses job-assistant instructions into a structured intent.

Three honest capabilities, deliberately NOT "apply to this link for me":

  - PROFILE:  set / show your reusable application data.
  - POSTING:  save a job posting, then match it against your resume. Reading
              and comparing -- SAFE, changes nothing.
  - APPLY:    fill a plain form from your profile and PREVIEW it. The actual
              submit still goes through the browser agent's gated SUBMIT, so
              the irreversible act is never taken without you seeing exactly
              what will be sent.
  - TRACK:    record and list applications you've made.

Risk: almost everything is SAFE. Setting a profile value is SENSITIVE
(it changes what SARVOS will later type into a page). Nothing here is
DESTRUCTIVE -- the one irreversible step, submitting an application, lives
in the browser agent behind its existing confirmation gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    ADD_CANDIDATE = "add_candidate"
    USE_CANDIDATE = "use_candidate"
    LIST_CANDIDATES = "list_candidates"
    WHOAMI = "whoami"
    SET_PROFILE = "set_profile"
    SHOW_PROFILE = "show_profile"
    SAVE_POSTING = "save_posting"
    MATCH_POSTING = "match_posting"
    OPTIMIZE_RESUME = "optimize_resume"
    REWRITE_RESUME = "rewrite_resume"
    COVER_LETTER = "cover_letter"
    FILL_FORM = "fill_form"
    LOG_APPLICATION = "log_application"
    LIST_APPLICATIONS = "list_applications"
    UNKNOWN = "unknown"


@dataclass
class JobIntent:
    operation: Operation
    risk: RiskLevel
    field: str | None = None       # profile field name
    value: str | None = None       # profile field value / posting name / company
    candidate: str | None = None   # candidate name
    raw_instruction: str = ""


def _mk(op: Operation, risk: RiskLevel, **kw) -> JobIntent:
    return JobIntent(operation=op, risk=risk, **kw)


# --- candidates ----------------------------------------------------------
_ADD_CANDIDATE_RE = re.compile(
    r"^(?:add|create|new)\s+candidate\s+['\"]?([\w\-]+)['\"]?$", re.I
)
_USE_CANDIDATE_RE = re.compile(
    r"^(?:use|switch\s+to|select)\s+candidate\s+['\"]?([\w\-]+)['\"]?$", re.I
)
_LIST_CANDIDATES_RE = re.compile(
    r"^(?:list|show)\s+(?:all\s+|my\s+)?candidates$", re.I
)
_WHOAMI_RE = re.compile(
    r"^(?:who(?:'s| is)\s+(?:the\s+)?active\s+candidate|which\s+candidate(?:\s+is\s+active)?)\??$",
    re.I,
)


# 'set my email to x@y.com'  /  'set profile phone to 555-1234'
_SET_RE = re.compile(
    r"^set\s+(?:my\s+|profile\s+)?([\w ]+?)\s+to\s+(.+)$", re.I
)
_SHOW_PROFILE_RE = re.compile(
    r"^(?:show|list|view)\s+(?:my\s+)?(?:job\s+)?profile$", re.I
)

# 'save this posting as senior-eng'  /  'save the job posting as x'
_SAVE_POSTING_RE = re.compile(
    r"^save\s+(?:this\s+|the\s+)?(?:job\s+)?posting\s+as\s+['\"]?([\w\- ]+)['\"]?$", re.I
)
# 'optimize my resume for senior-eng' / 'analyze my resume against senior-eng'
_OPTIMIZE_RE = re.compile(
    r"^(?:optimi[sz]e|analy[sz]e|tailor)\s+(?:my\s+|the\s+)?(?:resume|cv)\s+"
    r"(?:for|against|to)\s+['\"]?([\w\- ]+?)['\"]?$",
    re.I,
)
# 'rewrite my resume for senior-eng' / 'generate a tailored resume for X'
_REWRITE_RE = re.compile(
    r"^(?:rewrite|generate|create|write)\s+(?:a\s+)?(?:tailored\s+|new\s+|ats[\-\s]?friendly\s+)?"
    r"(?:my\s+|the\s+)?resume\s+(?:for|against|to)\s+['\"]?([\w\- ]+?)['\"]?$",
    re.I,
)
# 'write a cover letter for senior-eng'
_COVER_LETTER_RE = re.compile(
    r"^(?:write|generate|create|draft)\s+(?:a\s+)?cover\s+letter\s+"
    r"(?:for|against|to)\s+['\"]?([\w\- ]+?)['\"]?$",
    re.I,
)

# 'match senior-eng against my resume'  /  'match X against her resume sfdc.docx'
_MATCH_RE = re.compile(
    r"^match\s+(?:the\s+)?(?:posting\s+)?['\"]?([\w\- ]+?)['\"]?\s+"
    r"(?:against|to|with)\s+(?:my|her|his|their|the)?\s*(?:resume|cv)(?:\s+(.+\.\w{2,5}))?$",
    re.I,
)

# 'fill this form from my profile'  /  'fill the application from my profile'
_FILL_RE = re.compile(
    r"^fill\s+(?:this|the)\s+(?:form|application)\s+(?:from|with)\s+(?:my\s+)?profile$",
    re.I,
)

# 'log an application to Acme'  /  'log application: Acme - Senior Engineer'
_LOG_RE = re.compile(
    r"^(?:log|record|track)\s+(?:an?\s+)?application(?:\s+(?:to|for|at))?\s*[:\-]?\s*(.+)$",
    re.I,
)
_LIST_APPS_RE = re.compile(
    r"^(?:list|show|view)\s+(?:my\s+)?applications$", re.I
)


def classify(instruction: str) -> JobIntent:
    text = instruction.strip()

    # Candidate management, checked first -- most specific.
    m = _ADD_CANDIDATE_RE.match(text)
    if m:
        return _mk(Operation.ADD_CANDIDATE, RiskLevel.SAFE,
                   candidate=m.group(1).strip(), raw_instruction=instruction)
    m = _USE_CANDIDATE_RE.match(text)
    if m:
        return _mk(Operation.USE_CANDIDATE, RiskLevel.SAFE,
                   candidate=m.group(1).strip(), raw_instruction=instruction)
    if _LIST_CANDIDATES_RE.match(text):
        return _mk(Operation.LIST_CANDIDATES, RiskLevel.SAFE, raw_instruction=instruction)
    if _WHOAMI_RE.match(text):
        return _mk(Operation.WHOAMI, RiskLevel.SAFE, raw_instruction=instruction)

    if _SHOW_PROFILE_RE.match(text):
        return _mk(Operation.SHOW_PROFILE, RiskLevel.SAFE, raw_instruction=instruction)

    if _LIST_APPS_RE.match(text):
        return _mk(Operation.LIST_APPLICATIONS, RiskLevel.SAFE, raw_instruction=instruction)

    m = _SET_RE.match(text)
    if m:
        # Normalise "email address" -> "email", "phone number" -> "phone".
        field = m.group(1).strip().lower().replace(" ", "_")
        return _mk(Operation.SET_PROFILE, RiskLevel.SENSITIVE,
                   field=field, value=m.group(2).strip(), raw_instruction=instruction)

    m = _SAVE_POSTING_RE.match(text)
    if m:
        return _mk(Operation.SAVE_POSTING, RiskLevel.SAFE,
                   value=m.group(1).strip(), raw_instruction=instruction)

    m = _OPTIMIZE_RE.match(text)
    if m:
        return _mk(Operation.OPTIMIZE_RESUME, RiskLevel.SAFE,
                   value=m.group(1).strip(), raw_instruction=instruction)

    m = _REWRITE_RE.match(text)
    if m:
        # Writes a file -> SENSITIVE.
        return _mk(Operation.REWRITE_RESUME, RiskLevel.SENSITIVE,
                   value=m.group(1).strip(), raw_instruction=instruction)

    m = _COVER_LETTER_RE.match(text)
    if m:
        return _mk(Operation.COVER_LETTER, RiskLevel.SENSITIVE,
                   value=m.group(1).strip(), raw_instruction=instruction)

    m = _MATCH_RE.match(text)
    if m:
        return _mk(Operation.MATCH_POSTING, RiskLevel.SAFE,
                   value=m.group(1).strip(), field=(m.group(2) or "").strip() or None,
                   raw_instruction=instruction)

    if _FILL_RE.match(text):
        return _mk(Operation.FILL_FORM, RiskLevel.SAFE, raw_instruction=instruction)

    m = _LOG_RE.match(text)
    if m:
        return _mk(Operation.LOG_APPLICATION, RiskLevel.SAFE,
                   value=m.group(1).strip(), raw_instruction=instruction)

    return _mk(Operation.UNKNOWN, RiskLevel.SAFE, raw_instruction=instruction)


def looks_like_job_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
