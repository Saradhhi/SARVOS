"""
General Agent — fallback conversational agent for anything not routed to a
specialist (Coding, Memory). Backed by the same local Ollama LLM as the
Coding agent (free, no API key).

Includes recent conversation history as context so replies aren't
single-turn-blind — without this, every message would be answered as if it
were the first thing you'd ever said to SARVOS.
"""

from __future__ import annotations

from core.schemas import AgentName, AgentResult, Task
from agents.base import BaseAgent
from llm.client import LLMUnavailable, get_llm_client

SYSTEM_PROMPT = (
    "You are SARVOS, a calm, confident, helpful personal AI assistant. Be "
    "concise by default, detailed when asked. Never pretend to know "
    "something you don't; acknowledge uncertainty plainly."
)

HISTORY_TURNS = 6  # how much recent conversation to include as context


class GeneralAgent(BaseAgent):
    name = AgentName.GENERAL

    def handle(self, task: Task) -> AgentResult:
        prompt = self._build_prompt(task.instruction)
        try:
            client = get_llm_client()
            response = client.generate(prompt, system=SYSTEM_PROMPT)
        except LLMUnavailable as e:
            response = (
                f"[general-agent: local LLM unavailable] {e}\n\n"
                f"Once Ollama is running, I'll actually respond to: "
                f"'{task.instruction}'"
            )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True, output=response,
        )

    def _build_prompt(self, instruction: str) -> str:
        history = self.memory.recent_history(limit=HISTORY_TURNS)
        if not history:
            return instruction
        transcript = "\n".join(f"{t.role}: {t.content}" for t in history)
        return f"Recent conversation:\n{transcript}\n\nuser: {instruction}"
