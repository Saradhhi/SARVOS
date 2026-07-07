import tempfile
from pathlib import Path

import pytest

from agents.system_info import SystemInfoAgent, _format_bytes
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str) -> Task:
    return Task(parent_request_id="r1", agent=AgentName.SYSTEM_INFO, instruction=instruction)


@pytest.fixture
def agent():
    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryEngine(store=Store(Path(tmp) / "test.db"))
        yield SystemInfoAgent(memory)


def test_format_bytes_gb():
    assert _format_bytes(5 * 1024 ** 3) == "5.0 GB"


def test_format_bytes_mb_for_small_values():
    assert _format_bytes(500 * 1024 ** 2) == "500 MB"


def test_cpu_query_returns_real_data(agent):
    result = agent.handle(_task("check my cpu usage"))
    assert result.success
    assert "CPU" in result.output
    assert "core" in result.output
    assert isinstance(result.data["cpu_percent"], float)
    assert result.data["cpu_count"] >= 1


def test_ram_query_returns_real_data(agent):
    result = agent.handle(_task("how much ram do I have"))
    assert result.success
    assert "RAM" in result.output
    assert result.data["ram_total"] > 0
    assert 0 <= result.data["ram_percent"] <= 100


def test_disk_query_returns_real_data(agent):
    result = agent.handle(_task("check my disk usage"))
    assert result.success
    assert "Disk" in result.output
    assert "free" in result.output
    assert result.data["disk_total"] > 0


def test_battery_query_handles_no_battery_gracefully(agent):
    """Real bug found from running on different real hardware: this
    sandbox has no battery (headless Linux container), but a real laptop
    (like the one this was tested on) DOES have one -- hitting a
    different code branch entirely. The original version of this test
    only asserted the no-battery shape, and the two branches returned
    inconsistently-shaped data (one had a 'battery' key, the other had
    'battery_percent'/'power_plugged' at the top level) -- a real API
    bug, not just a test gap. Fixed by standardizing both branches to
    always return a 'battery' key with a consistent {present, percent,
    power_plugged} shape, and this test now correctly handles either
    real case rather than assuming only one is ever true."""
    result = agent.handle(_task("check my battery"))
    assert result.success
    battery = result.data["battery"]
    assert "present" in battery

    if battery["present"]:
        assert 0 <= battery["percent"] <= 100
        assert isinstance(battery["power_plugged"], bool)
        assert "%" in result.output
    else:
        assert battery["percent"] is None
        assert "desktop" in result.output.lower() or "no battery" in result.output.lower()


def test_network_query_returns_real_data(agent):
    result = agent.handle(_task("what's my network status"))
    assert result.success
    assert "sent" in result.output.lower()
    assert result.data["bytes_sent"] >= 0
    assert result.data["bytes_recv"] >= 0


def test_all_query_includes_everything(agent):
    result = agent.handle(_task("system info"))
    assert result.success
    assert "CPU" in result.output
    assert "RAM" in result.output
    assert "Disk" in result.output
    assert "cpu" in result.data
    assert "ram" in result.data
    assert "disk" in result.data
    assert "battery" in result.data


def test_unrecognized_instruction_gives_helpful_message(agent):
    result = agent.handle(_task("do something system-ish but vague"))
    assert not result.success
    assert "cpu" in result.output.lower()
