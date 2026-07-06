import tempfile
from pathlib import Path

import pytest

from core.factory import create_orchestrator
from voice.assistant import VoiceAssistant


@pytest.fixture
def assistant():
    with tempfile.TemporaryDirectory() as tmp:
        orchestrator = create_orchestrator(str(Path(tmp) / "test_voice.db"))
        yield VoiceAssistant(orchestrator)


def test_empty_utterance_handled_gracefully(assistant):
    response = assistant.handle_utterance("")
    assert "didn't catch" in response.lower()


def test_memory_utterance_routes_correctly(assistant):
    response = assistant.handle_utterance("remember that I like jazz")
    assert "jazz" in response


def test_recall_utterance_after_remembering(assistant):
    """Uses a query with real word overlap ('jazz'), consistent with the
    documented TF-IDF-is-lexical-not-semantic limitation (see
    tests/test_memory.py::test_tfidf_backend_is_lexical_not_semantic) —
    a query like 'my preferences' wouldn't match 'jazz' for the same
    reason it doesn't there, not because voice broke anything new."""
    assistant.handle_utterance("remember that I like jazz music")
    response = assistant.handle_utterance("what do you know about jazz")
    assert "jazz" in response.lower()


def test_destructive_utterance_asks_for_spoken_confirmation(assistant):
    response = assistant.handle_utterance("delete everything")
    assert "yes" in response.lower() and "no" in response.lower()
    assert assistant._pending_task is not None


def test_confirmation_approved_by_voice(assistant):
    assistant.handle_utterance("delete everything")
    response = assistant.handle_utterance("yes, go ahead")
    assert assistant._pending_task is None
    assert "won't" not in response.lower()  # not rejected


def test_confirmation_rejected_by_voice(assistant):
    assistant.handle_utterance("delete everything")
    response = assistant.handle_utterance("no, cancel that")
    assert assistant._pending_task is None
    assert "won't do that" in response.lower()


def test_ambiguous_response_to_confirmation_asks_again(assistant):
    assistant.handle_utterance("delete everything")
    response = assistant.handle_utterance("maybe later")
    assert assistant._pending_task is not None  # still pending
    assert "yes" in response.lower() and "no" in response.lower()


def test_confirmation_state_blocks_normal_routing_until_resolved(assistant):
    """While a confirmation is pending, the next utterance must be
    interpreted as yes/no, not routed through the planner again — even if
    it superficially looks like a new request."""
    assistant.handle_utterance("delete everything")
    # This would normally route to the memory agent, but must instead be
    # treated as an (ambiguous) answer to the pending confirmation.
    response = assistant.handle_utterance("remember that I like tea")
    assert assistant._pending_task is not None
