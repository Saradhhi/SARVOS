"""
Tests for ResearchAgent against DuckDuckGo's actual documented Instant
Answer API JSON schema (AbstractText/AbstractSource/AbstractURL,
Definition/DefinitionURL, Answer, RelatedTopics with Text/FirstURL) --
confirmed via current external documentation, not assumed.

Uses a fake requests.get (no real network needed, and this sandbox's
network is blocked from reaching DuckDuckGo entirely anyway) that returns
a controllable fake response object, so these test REAL parsing/formatting
logic against a REAL schema, just not a live network call.
"""

from __future__ import annotations

import types

import pytest

from agents.research import ResearchAgent
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str) -> Task:
    return Task(parent_request_id="r1", agent=AgentName.RESEARCH, instruction=instruction)


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200, raise_exc=None, json_exc=None):
        self._json_data = json_data
        self.status_code = status_code
        self._raise_exc = raise_exc
        self._json_exc = json_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._json_data


@pytest.fixture
def agent(tmp_path):
    memory = MemoryEngine(store=Store(tmp_path / "test.db"))
    yield ResearchAgent(memory)


def test_abstract_result(agent, monkeypatch):
    """Matches DDG's real schema for a well-known entity query."""
    fake_json = {
        "AbstractText": "Python is a high-level, general-purpose programming language.",
        "AbstractSource": "Wikipedia",
        "AbstractURL": "https://en.wikipedia.org/wiki/Python_(programming_language)",
        "Answer": "",
        "Definition": "",
        "RelatedTopics": [],
    }
    monkeypatch.setattr(
        "agents.research.requests.get", lambda *a, **k: _FakeResponse(fake_json)
    )
    result = agent.handle(_task("research python programming"))
    assert result.success
    assert "high-level" in result.output
    assert "Wikipedia" in result.output
    assert "en.wikipedia.org" in result.output


def test_answer_result(agent, monkeypatch):
    fake_json = {
        "AbstractText": "", "AbstractSource": "", "AbstractURL": "",
        "Answer": "42", "Definition": "", "RelatedTopics": [],
    }
    monkeypatch.setattr(
        "agents.research.requests.get", lambda *a, **k: _FakeResponse(fake_json)
    )
    result = agent.handle(_task("search for the answer to everything"))
    assert result.success
    assert "42" in result.output


def test_definition_result(agent, monkeypatch):
    fake_json = {
        "AbstractText": "", "AbstractSource": "", "AbstractURL": "",
        "Answer": "", "Definition": "A programming paradigm based on objects.",
        "DefinitionURL": "https://example.com/oop", "RelatedTopics": [],
    }
    monkeypatch.setattr(
        "agents.research.requests.get", lambda *a, **k: _FakeResponse(fake_json)
    )
    result = agent.handle(_task("look up object-oriented programming"))
    assert result.success
    assert "programming paradigm" in result.output
    assert "example.com/oop" in result.output


def test_related_topics_fallback(agent, monkeypatch):
    """When there's no direct Abstract/Answer/Definition, falls back to
    RelatedTopics -- DDG's real schema for these is {Text, FirstURL}."""
    fake_json = {
        "AbstractText": "", "AbstractSource": "", "AbstractURL": "",
        "Answer": "", "Definition": "",
        "RelatedTopics": [
            {"Text": "Linux kernel - the core of Linux operating systems",
             "FirstURL": "https://duckduckgo.com/Linux_kernel"},
            {"Text": "Linux distribution - a packaged version of Linux",
             "FirstURL": "https://duckduckgo.com/Linux_distribution"},
        ],
    }
    monkeypatch.setattr(
        "agents.research.requests.get", lambda *a, **k: _FakeResponse(fake_json)
    )
    result = agent.handle(_task("research linux"))
    assert result.success
    assert len(result.data["results"]) == 2
    assert "Linux kernel" in result.output


def test_nothing_found_is_honest_not_a_fake_success(agent, monkeypatch):
    """Real coverage gap, stated plainly: many reasonable queries return
    nothing from this API at all. Must say so honestly, with a pointer to
    a real search page, rather than pretending nothing was searched."""
    fake_json = {
        "AbstractText": "", "AbstractSource": "", "AbstractURL": "",
        "Answer": "", "Definition": "", "RelatedTopics": [],
    }
    monkeypatch.setattr(
        "agents.research.requests.get", lambda *a, **k: _FakeResponse(fake_json)
    )
    result = agent.handle(_task("research the latest news about quarterly earnings"))
    assert result.success  # not an error -- "found nothing" is a normal outcome
    assert result.data["results"] == []
    assert "duckduckgo.com" in result.output.lower()


def test_network_failure_fails_gracefully(agent, monkeypatch):
    import requests

    def raise_conn_error(*a, **k):
        raise requests.ConnectionError("simulated network failure")

    monkeypatch.setattr("agents.research.requests.get", raise_conn_error)
    result = agent.handle(_task("research anything"))
    assert not result.success
    assert result.error == "research_search_failed"


def test_invalid_json_fails_gracefully(agent, monkeypatch):
    monkeypatch.setattr(
        "agents.research.requests.get",
        lambda *a, **k: _FakeResponse(json_exc=ValueError("not valid json")),
    )
    result = agent.handle(_task("research anything"))
    assert not result.success
    assert result.error == "research_parse_failed"


def test_unrecognized_instruction_gives_helpful_message(agent):
    result = agent.handle(_task("do something research-ish but vague"))
    assert not result.success
    assert "research" in result.output.lower()
