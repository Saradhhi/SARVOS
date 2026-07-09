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
    """Regression test for a real gap found via live testing (not
    hypothesized in advance): 'what do you know about X' didn't match ANY
    of the Planner's memory-recall keywords, so it silently skipped
    memory retrieval entirely and went to general chat instead -- which
    has no access to stored facts. This test only appeared to pass before
    by accident: without Ollama running, the LLM-unavailable fallback
    happened to echo the input text verbatim (including the word
    "jazz"), masking that memory was never actually being consulted. It
    failed for real on a machine with Ollama running, which gave a
    genuine answer about jazz without happening to say the word "jazz"
    itself -- correctly exposing the routing gap.

    Fixed by broadening MEMORY_KEYWORDS (agents/planner.py) to include
    'what do you know', 'do you know', etc. This test now verifies real
    routing to the Memory agent (confirmed via handle_utterance's actual
    output format, "Here's what I remember: ..."), not accidental
    string overlap."""
    assistant.handle_utterance("remember that I like jazz music")
    response = assistant.handle_utterance("what do you know about jazz")
    assert "jazz" in response.lower()
    assert "remember" in response.lower()  # confirms this went through
    # the Memory agent's real recall path, not general chat happening to
    # mention jazz for some unrelated reason


def test_destructive_utterance_asks_for_spoken_confirmation(assistant):
    response = assistant.handle_utterance("delete everything")
    assert "yes" in response.lower() and "no" in response.lower()
    assert assistant._pending_task is not None


def test_confirmation_approved_by_voice(assistant, monkeypatch):
    """Asserts on the confirmation MACHINERY, not on LLM prose.

    The original version checked that the response text didn't contain
    "won't". That made the test's outcome depend on whether Ollama happened
    to be running and what it chose to say -- it passed in a sandbox with no
    LLM (stub fallback) and failed on a real machine, where llama3.2
    answered "delete everything" with an improvised warning about points of
    no return that happened to contain the word "won't". The approval had
    worked correctly; only the assertion was wrong.
    """
    captured = {}
    real_resume = assistant.orchestrator.resume_with_confirmation

    def spy(task, approved, request_id):
        captured["approved"] = approved
        return real_resume(task, approved, request_id)

    monkeypatch.setattr(assistant.orchestrator, "resume_with_confirmation", spy)

    assistant.handle_utterance("delete everything")
    assert assistant._pending_task is not None

    assistant.handle_utterance("yes, go ahead")
    assert assistant._pending_task is None
    assert captured["approved"] is True


def test_confirmation_rejected_by_voice(assistant, monkeypatch):
    """Unlike the approval path, the rejection message ("Okay, I won't do
    that.") is hardcoded in core/orchestrator.py, not LLM-generated -- so
    checking it is legitimate. The machinery is asserted too, so this stays
    correct if the wording ever changes."""
    captured = {}
    real_resume = assistant.orchestrator.resume_with_confirmation

    def spy(task, approved, request_id):
        captured["approved"] = approved
        return real_resume(task, approved, request_id)

    monkeypatch.setattr(assistant.orchestrator, "resume_with_confirmation", spy)

    assistant.handle_utterance("delete everything")
    response = assistant.handle_utterance("no, cancel that")
    assert assistant._pending_task is None
    assert captured["approved"] is False
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
