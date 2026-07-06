"""
Single source of truth for wiring up MemoryEngine + agents + Orchestrator.

Before this existed, main.py (CLI) and api/server.py (web UI) each built this
wiring separately. Adding voice as a third caller made that duplication a
real risk: forgetting to register a new agent in one of three places is an
easy, silent bug. Everything that needs a working SARVOS instance —
CLI, web API, voice assistant — should call `create_orchestrator()` here.
"""

from __future__ import annotations

from core.orchestrator import Orchestrator
from core.schemas import AgentName
from memory.engine import MemoryEngine
from memory.store import Store
from agents.planner import PlannerAgent
from agents.coding import CodingAgent
from agents.memory_agent import MemoryAgent
from agents.general import GeneralAgent


def create_orchestrator(db_path: str = "sarvos.db") -> Orchestrator:
    memory = MemoryEngine(store=Store(db_path))
    agents = {
        AgentName.PLANNER: PlannerAgent(memory),
        AgentName.CODING: CodingAgent(memory),
        AgentName.MEMORY: MemoryAgent(memory),
        AgentName.GENERAL: GeneralAgent(memory),
    }
    return Orchestrator(memory, agents)
