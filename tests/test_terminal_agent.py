import tempfile
from pathlib import Path

import pytest

from agents.terminal import TerminalAgent
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str) -> Task:
    return Task(parent_request_id="r1", agent=AgentName.TERMINAL, instruction=instruction)


@pytest.fixture
def agent():
    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryEngine(store=Store(Path(tmp) / "test.db"))
        yield TerminalAgent(memory)


def test_processes_returns_real_running_processes(agent):
    result = agent.handle(_task("show me the running processes"))
    assert result.success
    assert result.data["process_count"] > 0
    assert len(result.data["top_processes"]) > 0
    # This very test process should itself be somewhere in the real list.
    assert "process_count" in result.data


def test_current_user_returns_real_user(agent):
    result = agent.handle(_task("whoami"))
    assert result.success
    assert result.data["user"]
    assert result.data["user"] in result.output


def test_hostname_returns_real_hostname(agent):
    result = agent.handle(_task("what's my hostname"))
    assert result.success
    assert result.data["hostname"]
    assert result.data["hostname"] in result.output


def test_os_version_returns_real_version(agent):
    result = agent.handle(_task("what os version am I running"))
    assert result.success
    assert result.data["os_version"]
    assert result.data["system"]


def test_unrecognized_instruction_gives_helpful_message(agent):
    result = agent.handle(_task("do something terminal-ish but vague"))
    assert not result.success
    assert "whoami" in result.output.lower() or "processes" in result.output.lower()
