"""
Splits a response into sentences so it can be spoken one at a time, with an
interruption check between each — see voice/assistant.py's
_speak_with_interruption_checks. Pure text logic, fully testable without
any audio hardware.
"""

from __future__ import annotations

import re

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_into_sentences(text: str) -> list[str]:
    """Splits on sentence-ending punctuation followed by whitespace.
    Deliberately simple (no abbreviation handling like 'Dr.' or 'e.g.') --
    good enough for LLM-generated conversational text, where a slightly
    wrong split just means one interruption check happens a sentence early
    or late, not a correctness problem."""
    text = text.strip()
    if not text:
        return []
    parts = _SENTENCE_BOUNDARY.split(text)
    return [p.strip() for p in parts if p.strip()]
