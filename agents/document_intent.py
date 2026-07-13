"""
Parses document-intelligence instructions into a structured intent.

All operations are SAFE: reading a file changes nothing. The risk here isn't
destruction, it's disclosure -- so the real protection is the sandbox
(document_config.DOCUMENTS_DIR), not a confirmation prompt. Asking "are you
sure?" before reading a file you just named is theatre; refusing to read
outside the documents directory is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    LIST = "list"
    READ = "read"
    SUMMARIZE = "summarize"
    SEARCH = "search"
    UNKNOWN = "unknown"


@dataclass
class DocumentIntent:
    operation: Operation
    risk: RiskLevel
    filename: str | None = None
    query: str | None = None
    raw_instruction: str = ""


def _mk(op: Operation, **kw) -> DocumentIntent:
    return DocumentIntent(operation=op, risk=RiskLevel.SAFE, **kw)


# A filename must actually look like one -- it needs an extension. Same
# lesson as the browser's _normalize_url, which happily turned "how to
# reverse a list in python" into a hostname because nothing checked shape.
#
# Path separators ARE allowed, so that "read ../../../etc/passwd.txt" parses
# and is then explicitly REFUSED by resolve_safe_path. Rejecting traversal by
# failing to match the regex would be defense by accident: it leaves the real
# sandbox check untested and reports a confusing "I don't understand" instead
# of "refusing to read outside the documents directory". It also lets real
# subdirectories work ("read contracts/2024.pdf").
_FILE = r"[\w\-. ()/\\]+\.\w{2,5}"

_LIST_RE = re.compile(
    r"^(?:list|show(?:\s+me)?)\s+(?:my\s+|the\s+|all\s+)?documents$", re.I
)

_READ_RE = re.compile(
    rf"^(?:read|open|extract(?:\s+the)?\s+text\s+from|show(?:\s+me)?(?:\s+the)?"
    rf"(?:\s+contents\s+of)?)\s+['\"]?({_FILE})['\"]?$",
    re.I,
)

_SUMMARIZE_RE = re.compile(
    rf"^(?:summari[sz]e|give\s+me\s+a\s+summary\s+of|tl;?dr)\s+['\"]?({_FILE})['\"]?$",
    re.I,
)

# 'search resume.pdf for "python"' / 'find "python" in resume.pdf'
_SEARCH_IN_RE = re.compile(
    rf"^search\s+['\"]?({_FILE})['\"]?\s+for\s+['\"]?(.+?)['\"]?$", re.I
)
_FIND_IN_RE = re.compile(
    rf"^(?:find|look\s+for)\s+['\"](.+?)['\"]\s+in\s+['\"]?({_FILE})['\"]?$", re.I
)


def classify(instruction: str) -> DocumentIntent:
    text = instruction.strip()

    if _LIST_RE.match(text):
        return _mk(Operation.LIST, raw_instruction=instruction)

    m = _SEARCH_IN_RE.match(text)
    if m:
        return _mk(Operation.SEARCH, filename=m.group(1).strip(),
                   query=m.group(2).strip(), raw_instruction=instruction)

    m = _FIND_IN_RE.match(text)
    if m:
        return _mk(Operation.SEARCH, filename=m.group(2).strip(),
                   query=m.group(1).strip(), raw_instruction=instruction)

    m = _SUMMARIZE_RE.match(text)
    if m:
        return _mk(Operation.SUMMARIZE, filename=m.group(1).strip(),
                   raw_instruction=instruction)

    m = _READ_RE.match(text)
    if m:
        return _mk(Operation.READ, filename=m.group(1).strip(),
                   raw_instruction=instruction)

    return _mk(Operation.UNKNOWN, raw_instruction=instruction)


def looks_like_document_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
