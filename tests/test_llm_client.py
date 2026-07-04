"""
These tests run against a real (absent) Ollama instance — there's no
Ollama server in this environment, which makes it a genuine test of the
unavailable path rather than a mock standing in for one. On a machine
with Ollama actually running, `test_is_available_false_when_unreachable`
and `test_generate_raises_when_unreachable` would need Ollama stopped to
still pass as-is; that's expected and documented here rather than silently
skipped.
"""

from __future__ import annotations

from agents.coding import CodingAgent
from agents.general import GeneralAgent
from core.schemas import AgentName, Task
from llm.client import LLMUnavailable, OllamaClient
from memory.engine import MemoryEngine
from memory.store import Store
import tempfile
from pathlib import Path

import pytest


UNREACHABLE_HOST = "http://localhost:1"  # nothing listens here


def test_is_available_false_when_unreachable():
    client = OllamaClient(host=UNREACHABLE_HOST, timeout=1)
    assert client.is_available() is False


def test_generate_raises_llm_unavailable_when_unreachable():
    client = OllamaClient(host=UNREACHABLE_HOST, timeout=1)
    with pytest.raises(LLMUnavailable):
        client.generate("hello")


@pytest.fixture
def memory():
    with tempfile.TemporaryDirectory() as tmp:
        yield MemoryEngine(store=Store(Path(tmp) / "test.db"))


def test_coding_agent_falls_back_gracefully_when_llm_unavailable(memory, monkeypatch):
    """The agent must never crash or silently fabricate an answer when the
    LLM backend is unreachable — it should say so, clearly."""
    monkeypatch.setattr(
        "agents.coding.get_llm_client", lambda: OllamaClient(host=UNREACHABLE_HOST, timeout=1)
    )
    agent = CodingAgent(memory)
    result = agent.handle(
        Task(parent_request_id="r1", agent=AgentName.CODING, instruction="write a sort function")
    )
    assert result.success  # degraded, not crashed
    assert "unavailable" in result.output.lower()


def test_general_agent_falls_back_gracefully_when_llm_unavailable(memory, monkeypatch):
    monkeypatch.setattr(
        "agents.general.get_llm_client", lambda: OllamaClient(host=UNREACHABLE_HOST, timeout=1)
    )
    agent = GeneralAgent(memory)
    result = agent.handle(
        Task(parent_request_id="r1", agent=AgentName.GENERAL, instruction="how's it going?")
    )
    assert result.success
    assert "unavailable" in result.output.lower()
