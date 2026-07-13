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
    NEW_TAB = "new_tab"
    LIST_TABS = "list_tabs"
    SWITCH_TAB = "switch_tab"
    CLOSE_TAB = "close_tab"
    DOWNLOAD = "download"
    SAVE_PDF = "save_pdf"
    BOOKMARK = "bookmark"
    LIST_BOOKMARKS = "list_bookmarks"
    OPEN_BOOKMARK = "open_bookmark"
    CHECK_CHANGES = "check_changes"
    TYPE = "type"
    UPLOAD = "upload"
    PREVIEW = "preview"
    INSPECT = "inspect"
    AUTOFILL = "autofill"
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
    text_arg: str | None = None      # text to type, click target, or filename
    field_arg: str | None = None     # which field to type into / upload to
    tab_index: int | None = None     # 0-based, parsed from 1-based user input
    name_arg: str | None = None      # bookmark name / candidate for autofill
    raw_instruction: str = ""


# Reuses the read-only browser's URL normalizer rather than duplicating it.
# That one carries real, hard-won behavior: it blocks javascript:/file:/data:
# schemes, and it refuses text that isn't shaped like a host at all (found
# from real use -- "show me how to reverse a list in python" was being turned
# into "https://how to reverse a list in python" and navigated to). A second
# copy here would inevitably drift out of sync with those fixes.
from agents.browser_intent import _normalize_url


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
# --- Tabs ---------------------------------------------------------------
_NEW_TAB_RE = re.compile(
    r"^(?:open\s+(?:a\s+)?new\s+tab|new\s+tab)(?:\s+(?:at|to)\s+(.+))?$", re.I
)
_LIST_TABS_RE = re.compile(
    r"^(?:list|show(?:\s+me)?)\s+(?:my\s+|the\s+|all\s+)?tabs$|^what\s+tabs\s+are\s+open\??$",
    re.I,
)
_SWITCH_TAB_RE = re.compile(r"^switch\s+to\s+tab\s+(\d+)$", re.I)
_CLOSE_TAB_RE = re.compile(r"^close\s+tab\s+(\d+)$", re.I)

# --- Downloads and PDF ---------------------------------------------------
# Link text must be QUOTED, or be a bare filename. Its own routing test
# caught the loose version stealing "download the latest version of python",
# which is a question, not a browser command. Quoting matches how `type "x"
# into the name field` already works.
_DOWNLOAD_RE = re.compile(
    r"^download\s+(?:the\s+)?(?:file\s+|link\s+)?"
    r"(?:['\"](.+?)['\"]|([\w\-. ]+\.\w{2,5}))$",
    re.I,
)
_SAVE_PDF_RE = re.compile(
    r"^(?:save|print)\s+(?:the\s+|this\s+)?page\s+(?:as|to)\s+(?:a\s+)?pdf$", re.I
)

# --- Bookmarks -----------------------------------------------------------
# An explicit name is REQUIRED. The loose version turned "bookmark this for
# later" into a bookmark named "this for later".
_BOOKMARK_RE = re.compile(
    r"^bookmark\s+(?:this(?:\s+page)?\s+)?as\s+['\"]?([\w\-]+)['\"]?$", re.I
)
_LIST_BOOKMARKS_RE = re.compile(
    r"^(?:list|show(?:\s+me)?)\s+(?:my\s+|the\s+|all\s+)?bookmarks$", re.I
)
_OPEN_BOOKMARK_RE = re.compile(r"^open\s+(?:the\s+)?bookmark\s+['\"]?([\w\- ]+)['\"]?$", re.I)

# --- Snapshot compare ----------------------------------------------------
# Deliberately NOT called "monitor". Monitoring implies something runs while
# you're away, which needs a scheduler (Tier 8). This checks on demand and
# reports what changed since the last check.
_CHECK_CHANGES_RE = re.compile(
    r"^(?:check|has)\s+(?:this\s+page|the\s+page|it)\s+(?:for\s+)?chang(?:es|ed)\??$", re.I
)

# 'inspect the form' / 'what fields are on this form' / 'list the fields'
_INSPECT_RE = re.compile(
    r"^(?:inspect\s+(?:the\s+|this\s+)?form|"
    r"(?:list|show)\s+(?:the\s+|all\s+)?(?:form\s+)?fields|"
    r"what\s+fields\s+(?:are\s+)?(?:on\s+)?(?:this\s+)?(?:form|page)\??)$",
    re.I,
)

# 'autofill from <candidate>'  /  'fill this form for alice'  /  'autofill'
_AUTOFILL_RE = re.compile(
    r"^(?:auto[\- ]?fill|fill\s+(?:this\s+|the\s+)?(?:form|application))"
    r"(?:\s+(?:from|for|as)\s+(?:candidate\s+)?['\"]?([\w\-]+)['\"]?)?$",
    re.I,
)

_CLICK_RE = re.compile(r"^click\s+(?:on\s+)?(?:the\s+)?['\"]?(.+?)['\"]?$", re.I)

# 'upload resume.pdf to the resume field'  /  'attach resume.pdf'
_UPLOAD_RE = re.compile(
    r"^(?:upload|attach)\s+['\"]?([\w.\-/\\ ]+?)['\"]?"
    r"(?:\s+(?:to|into)\s+(?:the\s+)?(.+?)(?:\s+field)?)?$",
    re.I,
)

_PREVIEW_RE = re.compile(
    r"^(?:preview(?:\s+the\s+form)?|dry.?run|"
    r"show\s+me\s+(?:the\s+)?(?:filled|completed)\s+form|"
    r"what\s+will\s+(?:be\s+)?submit(?:ted)?)\??$",
    re.I,
)

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

    # Checked BEFORE _OPEN_RE: "open a new tab at X" and "open bookmark X"
    # both begin with "open" and would otherwise be read as a URL to visit.
    m = _NEW_TAB_RE.match(text)
    if m:
        raw = (m.group(1) or "").strip()
        url = _normalize_url(raw) if raw else None
        if raw and not url:
            return InteractiveBrowserIntent(
                operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction,
            )
        return InteractiveBrowserIntent(
            operation=Operation.NEW_TAB, risk=RiskLevel.SAFE, url=url,
            raw_instruction=instruction,
        )

    m = _OPEN_BOOKMARK_RE.match(text)
    if m:
        return InteractiveBrowserIntent(
            operation=Operation.OPEN_BOOKMARK, risk=RiskLevel.SAFE,
            name_arg=m.group(1).strip(), raw_instruction=instruction,
        )

    if _LIST_TABS_RE.match(text):
        return InteractiveBrowserIntent(
            operation=Operation.LIST_TABS, risk=RiskLevel.SAFE, raw_instruction=instruction,
        )

    if _LIST_BOOKMARKS_RE.match(text):
        return InteractiveBrowserIntent(
            operation=Operation.LIST_BOOKMARKS, risk=RiskLevel.SAFE, raw_instruction=instruction,
        )

    m = _SWITCH_TAB_RE.match(text)
    if m:
        # Users count tabs from 1; we index from 0.
        return InteractiveBrowserIntent(
            operation=Operation.SWITCH_TAB, risk=RiskLevel.SAFE,
            tab_index=int(m.group(1)) - 1, raw_instruction=instruction,
        )

    # Before _CLOSE_RE, which closes the whole session.
    m = _CLOSE_TAB_RE.match(text)
    if m:
        return InteractiveBrowserIntent(
            operation=Operation.CLOSE_TAB, risk=RiskLevel.SENSITIVE,
            tab_index=int(m.group(1)) - 1, raw_instruction=instruction,
        )

    if _SAVE_PDF_RE.match(text):
        return InteractiveBrowserIntent(
            operation=Operation.SAVE_PDF, risk=RiskLevel.SENSITIVE, raw_instruction=instruction,
        )

    if _CHECK_CHANGES_RE.match(text):
        return InteractiveBrowserIntent(
            operation=Operation.CHECK_CHANGES, risk=RiskLevel.SAFE, raw_instruction=instruction,
        )

    m = _BOOKMARK_RE.match(text)
    if m:
        return InteractiveBrowserIntent(
            operation=Operation.BOOKMARK, risk=RiskLevel.SAFE,
            name_arg=m.group(1).strip(), raw_instruction=instruction,
        )

    m = _OPEN_RE.match(text)
    if m:
        url = _normalize_url(m.group(1))
        if url:
            return InteractiveBrowserIntent(
                operation=Operation.OPEN, risk=RiskLevel.SAFE, url=url,
                raw_instruction=instruction,
            )

    # Before _CLICK_RE, which is the loosest pattern here.
    m = _DOWNLOAD_RE.match(text)
    if m:
        return InteractiveBrowserIntent(
            operation=Operation.DOWNLOAD, risk=RiskLevel.SENSITIVE,
            text_arg=(m.group(1) or m.group(2)).strip(), raw_instruction=instruction,
        )

    m = _UPLOAD_RE.match(text)
    if m and ("." in m.group(1)):  # must look like a filename
        return InteractiveBrowserIntent(
            operation=Operation.UPLOAD, risk=RiskLevel.SENSITIVE,
            text_arg=m.group(1).strip(),
            field_arg=(m.group(2) or "").strip() or None,
            raw_instruction=instruction,
        )

    if _PREVIEW_RE.match(text):
        return InteractiveBrowserIntent(
            operation=Operation.PREVIEW, risk=RiskLevel.SAFE,
            raw_instruction=instruction,
        )

    if _INSPECT_RE.match(text):
        return InteractiveBrowserIntent(
            operation=Operation.INSPECT, risk=RiskLevel.SAFE,
            raw_instruction=instruction,
        )

    m = _AUTOFILL_RE.match(text)
    if m:
        return InteractiveBrowserIntent(
            operation=Operation.AUTOFILL, risk=RiskLevel.SENSITIVE,
            name_arg=(m.group(1) or "").strip() or None,
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
