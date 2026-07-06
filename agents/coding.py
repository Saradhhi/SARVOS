"""
Coding Agent — now backed by a real local LLM (Ollama, free/no API key) via
llm/client.py. It does NOT touch the filesystem/terminal — that's Phase 3
(Automation) territory, where Tool Runtime integrations (git, terminal, VS
Code) land. This agent only generates/explains code as text.

Note on confirmation: destructive-risk gating happens centrally in the
Orchestrator before this agent is ever dispatched (see core/orchestrator.py).
By the time `handle` runs, either the task was never risky or the user has
already approved it — this agent doesn't need to (and shouldn't have to)
re-implement that check.

Graceful degradation: if Ollama isn't installed/running, `handle` falls back
to a clearly-labeled stub response instead of raising — a missing local LLM
should never crash the CLI, it should just tell the user how to fix it.
"""

from __future__ import annotations

from core.schemas import AgentName, AgentResult, Task
from agents.base import BaseAgent
from llm.client import LLMUnavailable, get_llm_client

SYSTEM_PROMPT = (
    "You are a precise, concise coding assistant. When asked to write code, "
    "return the code in a fenced block plus a brief explanation. When asked "
    "to debug or refactor, point out the specific issue before proposing a "
    "fix. Do not pad your answer with unnecessary preamble."
)

SPOKEN_SYSTEM_PROMPT = (
    "You are a precise, concise coding assistant. Your response will be "
    "read aloud by text-to-speech, not displayed as text -- reading raw "
    "code syntax character-by-character out loud is unusable, so do NOT "
    "output code blocks, brackets, or symbols. Instead, describe what the "
    "code does or what the fix is in plain spoken language (e.g. 'the bug "
    "is that the loop never increments the counter, so it runs forever' "
    "rather than showing the actual code). Mention that they can switch to "
    "text mode to see the actual code. Keep it brief and conversational."
)


class CodingAgent(BaseAgent):
    name = AgentName.CODING

    def handle(self, task: Task) -> AgentResult:
        response = self._draft_response(task.instruction, task.context)
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            success=True,
            output=response,
        )

    def _draft_response(self, instruction: str, context: dict) -> str:
        system_prompt = SPOKEN_SYSTEM_PROMPT if context.get("spoken") else SYSTEM_PROMPT
        try:
            client = get_llm_client()
            return client.generate(instruction, system=system_prompt)
        except LLMUnavailable as e:
            # Explicit, honest fallback — never silently return a fabricated
            # "answer" when the real backend couldn't be reached.
            return (
                f"[coding-agent: local LLM unavailable] {e}\n\n"
                f"Once Ollama is running, I'll actually generate/debug code "
                f"for: '{instruction}'"
            )
