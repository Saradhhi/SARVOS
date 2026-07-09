"""
Parses INTERACTIVE browsing instructions into a structured intent --
distinct from browser_intent.py (the read-only open/screenshot agent),
kept separate so that agent's simple, safe scope stays clean.

Interactive browsing is inherently stateful and multi-step: open a page,
THEN type into a field on it, THEN click, THEN submit -- all against the
same live browser session held open across turns (see
agents/interactive_browser.py's BrowserSession).

Risk tiers:
- SAFE: open/navigate, read the page, type into a field, click a
  (non-submitting) element, close the session. Typing and clicking are
  SAFE because on their own they change nothing permanent -- the real
  consequence is at submit time.
- DESTRUCTIVE: submit a form / log in. This is where real, often
  irreversible side effects happen (authenticating, sending data,
  purchasing), so it's gated by the orchestrator's confirmation check
  before anything is actually submitted -- per an explicit decision to
  gate only the moment of consequence, not every harmless click.

CREDENTIALS: there is deliberately NO operation here for storing,
loading, or auto-filling saved passwords. Logging in means the person
types their credentials in the moment via a normal 'type' command;
nothing is ever persisted. See the module docstring in
interactive_browser.py for the honest security caveat about typing
credentials into a headless automated session at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    OPEN = "open"
    TYPE = "type"
    CLICK = "click"
    READ = "read"
    SUBMIT = "submit"
    CLOSE = "close"
    UNKNOWN = "unknown"


@dataclass
class InteractiveBrowserIntent:
    operation: Operation
    risk: RiskLevel
    url: str | None = None
    text_arg: str | None = None      # text to type, or click-target description
    field_arg: str | None = None     # which field to type into
    raw_instruction: str = ""


_HAS_ANY_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_ALLOWED_SCHEME_RE = re.compile(r"^https?://", re.I)


def _normalize_url(raw: str) -> str | None:
    raw = raw.strip().strip('"\'').rstrip(".,!?")
    if not raw:
        return None
    if not _HAS_ANY_SCHEME_RE.match(raw):
        raw = f"https://{raw}"
    if not _ALLOWED_SCHEME_RE.match(raw):
        return None
    return raw


# "open a browser session at X" / "start browsing X" / "browse to X"
_OPEN_RE = re.compile(
    r"^(?:open (?:a )?(?:browser )?session (?:at|on) |"
    r"start (?:a )?(?:browser )?session (?:at|on) |"
    r"start browsing |browse to )(.+)$",
    re.I,
)

# 'type "value" into the <field> field' / 'type "value" in <field>'
_TYPE_RE = re.compile(
    r"^type\s+['\"](.+?)['\"]\s+(?:into|in)\s+(?:the\s+)?(.+?)(?:\s+field)?$",
    re.I,
)

# 'click "the login button"' / 'click the submit link'
_CLICK_RE = re.compile(r"^click\s+(?:on\s+)?(?:the\s+)?['\"]?(.+?)['\"]?$", re.I)

_READ_RE = re.compile(
    r"^(?:read (?:the )?page|what'?s on (?:the |this )?page|read (?:it|this))$", re.I
)

# submit / log in -- the DESTRUCTIVE, gated operations
_SUBMIT_RE = re.compile(
    r"^(?:submit(?: the form)?|log ?in|sign ?in|press submit)$", re.I
)

_CLOSE_RE = re.compile(
    r"^(?:close (?:the )?(?:browser )?session|end (?:the )?session|"
    r"close (?:the )?browser|stop browsing)$",
    re.I,
)


def classify(instruction: str) -> InteractiveBrowserIntent:
    text = instruction.strip()

    m = _OPEN_RE.match(text)
    if m:
        url = _normalize_url(m.group(1))
        if url:
            return InteractiveBrowserIntent(
                operation=Operation.OPEN, risk=RiskLevel.SAFE, url=url,
                raw_instruction=instruction,
            )

    m = _TYPE_RE.match(text)
    if m:
        return InteractiveBrowserIntent(
            operation=Operation.TYPE, risk=RiskLevel.SAFE,
            text_arg=m.group(1), field_arg=m.group(2).strip(),
            raw_instruction=instruction,
        )

    if _READ_RE.match(text):
        return InteractiveBrowserIntent(
            operation=Operation.READ, risk=RiskLevel.SAFE, raw_instruction=instruction,
        )

    if _SUBMIT_RE.match(text):
        return InteractiveBrowserIntent(
            operation=Operation.SUBMIT, risk=RiskLevel.DESTRUCTIVE,
            raw_instruction=instruction,
        )

    if _CLOSE_RE.match(text):
        return InteractiveBrowserIntent(
            operation=Operation.CLOSE, risk=RiskLevel.SAFE, raw_instruction=instruction,
        )

    # Click is checked LAST because its pattern is the loosest (matches
    # almost anything after "click") -- everything more specific must win
    # first.
    m = _CLICK_RE.match(text)
    if m:
        return InteractiveBrowserIntent(
            operation=Operation.CLICK, risk=RiskLevel.SAFE,
            text_arg=m.group(1).strip(), raw_instruction=instruction,
        )

    return InteractiveBrowserIntent(
        operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction,
    )


def looks_like_interactive_browser_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
