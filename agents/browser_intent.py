"""
Parses browser-flavored instructions into a structured intent. Same
pattern as agents/automation_intent.py: deterministic parsing, not an LLM
freely deciding what to browse to, shared between the Planner (risk) and
the BrowserAgent (execution) so they can't disagree.

Scope for this version: read-only browsing only -- open a page, extract
its title/text, or screenshot it. NOT included: form filling/submission,
login, downloads, or clicking through multi-step flows. Those have real
side effects on external sites (submitting data, authenticating,
purchasing) that deserve their own careful, separately-scoped and tested
work, the same way file writes/deletes got their own careful treatment
rather than being bundled into the first pass at file automation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    OPEN_URL = "open_url"
    SCREENSHOT = "screenshot"
    UNKNOWN = "unknown"


@dataclass
class BrowserIntent:
    operation: Operation
    risk: RiskLevel
    url: str | None = None
    raw_instruction: str = ""


_SCREENSHOT_RE = re.compile(
    r"^(?:take\s+a\s+)?screenshot\s+of\s+(.+)$", re.I
)
# "show me" is deliberately NOT a bare trigger here: it's how people phrase
# ordinary questions ("show me how to reverse a list in python"), which
# really did get routed to the browser and turned into a bogus URL. It only
# counts when followed by an explicit web noun. The other verbs (open, go
# to, visit, browse to) are unambiguous enough to stand alone -- and
# _normalize_url now rejects anything that isn't shaped like a host anyway.
_OPEN_RE = re.compile(
    r"^(?:(?:open|go\s+to|visit|browse\s+to)\s+(?:the\s+)?"
    r"(?:website\s+|page\s+|site\s+)?"
    r"|show\s+me\s+(?:the\s+)?(?:website\s+|page\s+|site\s+))(.+)$",
    re.I,
)

# Detects ANY URI scheme (scheme:...), not just http(s) -- javascript:,
# data:, mailto: etc. don't use "//" after the colon, so a "//"-requiring
# check would misclassify them as scheme-less and let _normalize_url
# prepend "https://" to them, producing e.g. "https://javascript:alert(1)"
# which then WOULD pass _ALLOWED_SCHEME_RE below -- completely defeating
# the safety check. Caught by test_javascript_scheme_is_blocked actually
# failing, not by reasoning about it in advance.
_HAS_ANY_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")

# Only http(s) is allowed past that point. Explicitly blocking file://,
# javascript:, data:, etc. matters here -- without it, "open website
# file:///etc/passwd" or similar would let page-reading logic double as an
# arbitrary local file reader, sidestepping the sandboxing built for the
# file-automation agent.
_ALLOWED_SCHEME_RE = re.compile(r"^https?://", re.I)


# A bare host must actually look like one before we prepend https:// to it.
# Found from real use: "show me how to reverse a list in python" matched the
# open-URL phrasing, and _normalize_url happily turned the whole sentence
# into "https://how to reverse a list in python", which the browser agent
# then tried to navigate to (ERR_NAME_NOT_RESOLVED). Hostnames cannot
# contain whitespace, and a real bare host has a dot in it (example.com) --
# except localhost, which is worth allowing explicitly.
_BARE_HOST_RE = re.compile(
    r"^(?:localhost(?::\d+)?|[\w\-]+(?:\.[\w\-]+)+)(?::\d+)?(?:[/?#].*)?$",
    re.I,
)


def _normalize_url(raw: str) -> str | None:
    raw = raw.strip().strip('"\'').rstrip(".,!?")
    if not raw:
        return None

    # Checked BEFORE scheme detection: "localhost:8000" and "example.com:443"
    # contain a colon, so _HAS_ANY_SCHEME_RE would read "localhost" as a URI
    # scheme, skip the https:// prefix, and then get refused. Bare hosts
    # (with or without a port) are recognised here first.
    if _BARE_HOST_RE.match(raw):
        return f"https://{raw}"

    if not _HAS_ANY_SCHEME_RE.match(raw):
        # No scheme and it doesn't look like a host either -- this is an
        # ordinary sentence that happened to match the open-URL phrasing.
        # Turning it into a URL would be nonsense (really happened: "show
        # me how to reverse a list in python" became a navigation attempt).
        return None

    if not _ALLOWED_SCHEME_RE.match(raw):
        return None  # non-http(s) scheme -- refused, see module docstring
    return raw


def classify(instruction: str) -> BrowserIntent:
    text = instruction.strip()

    match = _SCREENSHOT_RE.match(text)
    if match:
        url = _normalize_url(match.group(1))
        if url is None:
            return BrowserIntent(
                operation=Operation.UNKNOWN, risk=RiskLevel.SAFE,
                raw_instruction=instruction,
            )
        return BrowserIntent(
            operation=Operation.SCREENSHOT, risk=RiskLevel.SAFE,
            url=url, raw_instruction=instruction,
        )

    match = _OPEN_RE.match(text)
    if match:
        url = _normalize_url(match.group(1))
        if url is None:
            return BrowserIntent(
                operation=Operation.UNKNOWN, risk=RiskLevel.SAFE,
                raw_instruction=instruction,
            )
        return BrowserIntent(
            operation=Operation.OPEN_URL, risk=RiskLevel.SAFE,
            url=url, raw_instruction=instruction,
        )

    return BrowserIntent(
        operation=Operation.UNKNOWN, risk=RiskLevel.SAFE, raw_instruction=instruction,
    )


def looks_like_browser_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
