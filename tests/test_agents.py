import tempfile
from pathlib import Path

import pytest

from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store
from agents.memory_agent import MemoryAgent


@pytest.fixture
def memory_agent() -> MemoryAgent:
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryEngine(store=Store(Path(tmp) / "test.db"))
        yield MemoryAgent(mem)


def _task(instruction: str) -> Task:
    return Task(parent_request_id="req1", agent=AgentName.MEMORY, instruction=instruction)


def test_remember_does_not_corrupt_trailing_characters(memory_agent: MemoryAgent):
    """Regression test: str.strip(' :,.that') strips individual CHARACTERS,
    not the word 'that' — it previously ate the trailing 'a' off of 'tea'
    because 'a' is itself in the strip set, storing 'I like te' instead of
    'I like tea'. Found via manual CLI smoke testing, not by the original
    unit tests, since they didn't happen to end a sentence in one of the
    stripped characters (a, t, h)."""
    result = memory_agent.handle(_task("remember that I like tea"))
    assert result.success
    assert "I like tea" in result.output
    assert "I like te\"" not in result.output


def test_remember_strips_leading_verb_and_that(memory_agent: MemoryAgent):
    result = memory_agent.handle(_task("remember I prefer dark mode"))
    assert "I prefer dark mode" in result.output


def test_remember_with_colon(memory_agent: MemoryAgent):
    result = memory_agent.handle(_task("remember: my birthday is in March"))
    assert "my birthday is in March" in result.output


def test_remember_empty_fact_is_rejected(memory_agent: MemoryAgent):
    result = memory_agent.handle(_task("remember that"))
    assert not result.success
