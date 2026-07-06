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
    "something you don't; acknowledge uncertainty plainly. Talk like a "
    "person, not a corporate assistant -- skip phrases like 'I'd be happy "
    "to help', 'Certainly!', or 'As an AI'; just answer directly, using "
    "contractions, like a knowledgeable friend would rather than a "
    "customer service script."
)

SPOKEN_SYSTEM_PROMPT = (
    "You are SARVOS. You're talking out loud to someone, not writing them "
    "a message -- talk like an actual person would, not like a corporate "
    "assistant. Concretely:\n"
    "- Never say things like 'I'd be happy to assist you with that', "
    "'Certainly!', 'I'm just a language model', 'As an AI', or any other "
    "stock customer-service phrase. Just answer, the way a knowledgeable "
    "friend would.\n"
    "- Use contractions (I'm, you're, don't, it's) -- that's how people "
    "actually talk.\n"
    "- Skip the hedging and disclaimers unless they're genuinely useful. "
    "If you don't know something, just say 'I don't know' or 'not sure', "
    "not a paragraph explaining your limitations as a language model.\n"
    "- Keep it short and conversational -- one or two sentences for most "
    "things, like a real back-and-forth, not a monologue.\n"
    "- No numbered lists, bullet points, or markdown -- this is speech, "
    "not a document. Weave options into a sentence instead of listing them.\n"
    "- It's fine to have a bit of personality and warmth. You don't need "
    "to sound neutral or corporate."
)

HISTORY_TURNS = 6  # how much recent conversation to include as context


class GeneralAgent(BaseAgent):
    name = AgentName.GENERAL

    def handle(self, task: Task) -> AgentResult:
        prompt = self._build_prompt(task.instruction)
        system_prompt = SPOKEN_SYSTEM_PROMPT if task.context.get("spoken") else SYSTEM_PROMPT
        try:
            client = get_llm_client()
            response = client.generate(prompt, system=system_prompt)
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
