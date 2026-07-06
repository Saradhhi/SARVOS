import tempfile
from pathlib import Path

import pytest

from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store
from agents.coding import CodingAgent, SYSTEM_PROMPT as CODING_TEXT_PROMPT, SPOKEN_SYSTEM_PROMPT as CODING_SPOKEN_PROMPT
from agents.general import GeneralAgent, SYSTEM_PROMPT as GENERAL_TEXT_PROMPT, SPOKEN_SYSTEM_PROMPT as GENERAL_SPOKEN_PROMPT


class FakeLLMClient:
    """Captures the system prompt it was called with, instead of actually
    calling Ollama -- lets us verify prompt SELECTION logic without
    depending on a real LLM backend being available."""

    def __init__(self):
        self.last_system_prompt = None

    def generate(self, prompt, system=None):
        self.last_system_prompt = system
        return "fake response"


@pytest.fixture
def memory():
    with tempfile.TemporaryDirectory() as tmp:
        yield MemoryEngine(store=Store(Path(tmp) / "test.db"))


def test_coding_agent_uses_spoken_prompt_when_flagged(memory, monkeypatch):
    fake_client = FakeLLMClient()
    monkeypatch.setattr("agents.coding.get_llm_client", lambda: fake_client)
    agent = CodingAgent(memory)
    task = Task(
        parent_request_id="r1", agent=AgentName.CODING,
        instruction="debug this", context={"spoken": True},
    )
    agent.handle(task)
    assert fake_client.last_system_prompt == CODING_SPOKEN_PROMPT


def test_coding_agent_uses_text_prompt_by_default(memory, monkeypatch):
    fake_client = FakeLLMClient()
    monkeypatch.setattr("agents.coding.get_llm_client", lambda: fake_client)
    agent = CodingAgent(memory)
    task = Task(
        parent_request_id="r1", agent=AgentName.CODING,
        instruction="debug this", context={},  # no spoken flag -- CLI/web path
    )
    agent.handle(task)
    assert fake_client.last_system_prompt == CODING_TEXT_PROMPT


def test_general_agent_uses_spoken_prompt_when_flagged(memory, monkeypatch):
    fake_client = FakeLLMClient()
    monkeypatch.setattr("agents.general.get_llm_client", lambda: fake_client)
    agent = GeneralAgent(memory)
    task = Task(
        parent_request_id="r1", agent=AgentName.GENERAL,
        instruction="how's it going", context={"spoken": True},
    )
    agent.handle(task)
    assert fake_client.last_system_prompt == GENERAL_SPOKEN_PROMPT


def test_general_agent_uses_text_prompt_by_default(memory, monkeypatch):
    fake_client = FakeLLMClient()
    monkeypatch.setattr("agents.general.get_llm_client", lambda: fake_client)
    agent = GeneralAgent(memory)
    task = Task(
        parent_request_id="r1", agent=AgentName.GENERAL,
        instruction="how's it going", context={},
    )
    agent.handle(task)
    assert fake_client.last_system_prompt == GENERAL_TEXT_PROMPT


def test_voice_assistant_flags_requests_as_spoken(memory, monkeypatch):
    """End-to-end: a real utterance through VoiceAssistant should result in
    the spoken prompt being used, not just when constructing a Task by
    hand in the tests above."""
    from core.factory import create_orchestrator
    from voice.assistant import VoiceAssistant

    fake_client = FakeLLMClient()
    monkeypatch.setattr("agents.general.get_llm_client", lambda: fake_client)

    with tempfile.TemporaryDirectory() as tmp:
        orchestrator = create_orchestrator(str(Path(tmp) / "test_voice2.db"))
        assistant = VoiceAssistant(orchestrator)
        assistant.handle_utterance("how's the weather")
        assert fake_client.last_system_prompt == GENERAL_SPOKEN_PROMPT
