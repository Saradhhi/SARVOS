"""
Memory Agent — the user-facing interface to MemoryEngine's semantic layer.

Handles: "remember that ...", "what do you know about ...", "forget ...".
This is intentionally simple pattern matching for Phase 1a; a real NLU layer
(or LLM-based intent extraction) can replace `_strip_leading_phrase` later
without changing the agent's contract with the orchestrator.
"""

from __future__ import annotations

import re

from core.schemas import AgentName, AgentResult, Task
from agents.base import BaseAgent


def _strip_leading_phrase(text: str, verb: str) -> str:
    """Remove a leading command verb and connective words (e.g. "remember",
    "that", ":") from the start of `text`, WITHOUT touching the remaining
    content. `str.strip(chars)` strips individual *characters*, not words or
    substrings — using it here previously corrupted stored text (e.g.
    "remember that I like tea" -> "I like te", because the trailing "a" in
    "tea" is itself one of the characters in the strip set). This regex-based
    version only removes whole leading words, so it can't eat into unrelated
    text later in the string."""
    pattern = rf"^\s*{re.escape(verb)}\b\s*(that\b)?\s*[:,]?\s*"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


class MemoryAgent(BaseAgent):
    name = AgentName.MEMORY

    def handle(self, task: Task) -> AgentResult:
        text = task.instruction.strip()
        lowered = text.lower()

        if lowered.startswith("remember"):
            fact = _strip_leading_phrase(text, "remember")
            if not fact:
                return AgentResult(
                    task_id=task.task_id, agent=self.name, success=False,
                    output="Tell me what to remember, e.g. 'remember that I prefer dark mode.'",
                )
            record = self.memory.remember(fact, kind="note")
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                success=True,
                output=f"Got it — I'll remember: \"{fact}\"",
                data={"record_id": record.record_id},
            )

        if lowered.startswith("forget"):
            fact = _strip_leading_phrase(text, "forget")
            matches = self.memory.recall(fact, top_k=1)
            if not matches:
                return AgentResult(
                    task_id=task.task_id, agent=self.name, success=False,
                    output=f"I couldn't find a memory matching \"{fact}\".",
                )
            record, _ = matches[0]
            self.memory.forget(record.record_id)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Forgotten: \"{record.text}\"",
            )

        # Default: treat as a recall query.
        results = self.memory.recall(text, top_k=3)
        if not results:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="I don't have any relevant memories yet.",
            )
        lines = [f"- {rec.text} (relevance {score:.2f})" for rec, score in results]
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output="Here's what I remember:\n" + "\n".join(lines),
        )
