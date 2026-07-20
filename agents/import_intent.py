"""
agents/import_intent.py

Parses import instructions. All SAFE -- reading a CSV and recording rows
changes nothing irreversible, and duplicates are skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    LIST_IMPORTS = "list_imports"
    IMPORT_FILE = "import_file"
    UNKNOWN = "unknown"


@dataclass
class ImportIntent:
    operation: Operation
    risk: RiskLevel
    filename: str | None = None
    raw_instruction: str = ""


_LIST_RE = re.compile(
    r"^(?:list|show)\s+(?:my\s+|the\s+)?import(?:s|\s+files?)?$", re.I
)

# 'import applications from loopcv.csv' / 'import from loopcv.csv'
_IMPORT_RE = re.compile(
    r"^import\s+(?:applications\s+|jobs\s+)?(?:from\s+)?['\"]?([\w\-. ()]+\.(?:csv|tsv))['\"]?$",
    re.I,
)


def classify(instruction: str) -> ImportIntent:
    text = instruction.strip()

    if _LIST_RE.match(text):
        return ImportIntent(Operation.LIST_IMPORTS, RiskLevel.SAFE,
                            raw_instruction=instruction)

    m = _IMPORT_RE.match(text)
    if m:
        return ImportIntent(Operation.IMPORT_FILE, RiskLevel.SAFE,
                            filename=m.group(1).strip(), raw_instruction=instruction)

    return ImportIntent(Operation.UNKNOWN, RiskLevel.SAFE, raw_instruction=instruction)


def looks_like_import_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
