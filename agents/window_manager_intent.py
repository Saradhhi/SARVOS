"""
Parses window-management instructions into a structured intent.

RISK TIERS:
- SAFE: list windows, get the active window. Read-only.
- SENSITIVE: focus, minimize, maximize, restore, move, resize. Real visible
  effects, but nothing is lost -- every one is trivially reversible by the
  person sitting at the machine.
- DESTRUCTIVE: close a window. Can lose unsaved work, so it goes through
  the orchestrator's real confirmation gate before anything happens.

Window targets are matched by a substring of the title, deliberately (see
agents/window_manager.py). "the active window" / "this window" refer to
whatever is currently focused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    LIST = "list"
    ACTIVE = "active"
    FOCUS = "focus"
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    RESTORE = "restore"
    MOVE = "move"
    RESIZE = "resize"
    CLOSE = "close"
    UNKNOWN = "unknown"


@dataclass
class WindowIntent:
    operation: Operation
    risk: RiskLevel
    target: str | None = None      # window title substring, or None = active
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    raw_instruction: str = ""


def _mk(op: Operation, risk: RiskLevel, **kw) -> WindowIntent:
    return WindowIntent(operation=op, risk=risk, **kw)


# Phrases meaning "whatever window is focused right now".
_ACTIVE_TARGET_RE = re.compile(r"^(?:the\s+)?(?:active|current|focused|this)\s+window$", re.I)


def _parse_target(raw: str | None) -> str | None:
    """None means 'the active window'. Anything else is a title substring."""
    if raw is None:
        return None
    raw = raw.strip().strip("\"'").rstrip(".!?")
    if not raw or _ACTIVE_TARGET_RE.match(raw):
        return None
    # Strip a leading "the " and a trailing " window", which people say
    # naturally ("minimize the notepad window").
    raw = re.sub(r"^the\s+", "", raw, flags=re.I)
    raw = re.sub(r"\s+window$", "", raw, flags=re.I)
    raw = raw.strip()
    # A bare "window" with nothing else (from "close window") is not a
    # title -- it means the active window.
    if not raw or raw.lower() == "window":
        return None
    return raw


_LIST_RE = re.compile(
    r"^(?:list|show(?:\s+me)?)\s+(?:all\s+|my\s+|the\s+)?(?:open\s+)?windows$", re.I
)
_ACTIVE_RE = re.compile(
    r"^(?:what(?:'s| is)\s+(?:the\s+)?(?:active|current|focused)\s+window|"
    r"which\s+window\s+is\s+(?:active|focused))\??$",
    re.I,
)

# These verbs are extremely common in ordinary speech ("focus on your work",
# "minimize the risk", "restore my faith in humanity", "move on to the next
# task"). Caught by their own negative tests. So each requires either:
#   - a bare verb, meaning the currently active window ("minimize"), or
#   - an explicit window noun ("minimize the notepad window", "focus the
#     chrome window"), or
#   - an explicit active-window phrase ("minimize the active window").
# A bare "minimize notepad" is also allowed, because a lone noun with no
# preposition reads unambiguously as a window title, unlike "focus on X".
_WINDOW_NOUN = r"(?:.+\s+window|(?:the\s+)?(?:active|current|focused|this)\s+window)"
_BARE_TITLE = r"[\w.\-]+"  # single token: "notepad", "chrome", "calc.exe"

_FOCUS_RE = re.compile(
    rf"^(?:focus(?:\s+on)?|switch\s+to|bring\s+up)\s+(?:{_WINDOW_NOUN}|{_BARE_TITLE})$",
    re.I,
)
_MINIMIZE_RE = re.compile(rf"^minimi[sz]e(?:\s+(?:{_WINDOW_NOUN}|{_BARE_TITLE}))?$", re.I)
_MAXIMIZE_RE = re.compile(rf"^maximi[sz]e(?:\s+(?:{_WINDOW_NOUN}|{_BARE_TITLE}))?$", re.I)
_RESTORE_RE = re.compile(
    rf"^(?:restore|unminimi[sz]e)(?:\s+(?:{_WINDOW_NOUN}|{_BARE_TITLE}))?$", re.I
)
# "close window" (active) or "close the <x> window".
_CLOSE_RE = re.compile(rf"^close\s+(?:the\s+)?(?:window|{_WINDOW_NOUN})$", re.I)

# "move <target> to 100, 200"  /  "move the window to 100 200"
_MOVE_RE = re.compile(
    r"^move\s+(.+?)\s+to\s+(-?\d+)\s*[, ]\s*(-?\d+)$", re.I
)
# "resize <target> to 800 by 600" / "... to 800x600" / "... to 800, 600"
_RESIZE_RE = re.compile(
    r"^resize\s+(.+?)\s+to\s+(\d+)\s*(?:x|by|,)\s*(\d+)$", re.I
)


_VERB_STRIP_RE = re.compile(
    r"^(?:focus(?:\s+on)?|switch\s+to|bring\s+up|minimi[sz]e|maximi[sz]e|"
    r"restore|unminimi[sz]e|close)\s*",
    re.I,
)


def _target_from(text: str) -> str | None:
    """Strip the leading verb, then normalise what remains into a title
    substring (or None, meaning the active window)."""
    return _parse_target(_VERB_STRIP_RE.sub("", text, count=1).strip() or None)


def classify(instruction: str) -> WindowIntent:
    text = instruction.strip()

    if _LIST_RE.match(text):
        return _mk(Operation.LIST, RiskLevel.SAFE, raw_instruction=instruction)
    if _ACTIVE_RE.match(text):
        return _mk(Operation.ACTIVE, RiskLevel.SAFE, raw_instruction=instruction)

    # Geometry ops before the bare verbs, since "move X to ..." would
    # otherwise be ambiguous with nothing.
    m = _MOVE_RE.match(text)
    if m:
        return _mk(Operation.MOVE, RiskLevel.SENSITIVE,
                   target=_parse_target(m.group(1)),
                   x=int(m.group(2)), y=int(m.group(3)), raw_instruction=instruction)

    m = _RESIZE_RE.match(text)
    if m:
        w, h = int(m.group(2)), int(m.group(3))
        if w <= 0 or h <= 0:
            return _mk(Operation.UNKNOWN, RiskLevel.SAFE, raw_instruction=instruction)
        return _mk(Operation.RESIZE, RiskLevel.SENSITIVE,
                   target=_parse_target(m.group(1)),
                   width=w, height=h, raw_instruction=instruction)

    # DESTRUCTIVE before the SENSITIVE verbs.
    if _CLOSE_RE.match(text):
        return _mk(Operation.CLOSE, RiskLevel.DESTRUCTIVE,
                   target=_target_from(text), raw_instruction=instruction)

    for op, pattern in (
        (Operation.MINIMIZE, _MINIMIZE_RE),
        (Operation.MAXIMIZE, _MAXIMIZE_RE),
        (Operation.RESTORE, _RESTORE_RE),
        (Operation.FOCUS, _FOCUS_RE),
    ):
        if pattern.match(text):
            return _mk(op, RiskLevel.SENSITIVE,
                       target=_target_from(text), raw_instruction=instruction)

    return _mk(Operation.UNKNOWN, RiskLevel.SAFE, raw_instruction=instruction)


def looks_like_window_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
