import tempfile
from pathlib import Path

import pytest

from core.factory import create_orchestrator
from voice.assistant import VoiceAssistant


@pytest.fixture
def assistant_with_events():
    events = []
    with tempfile.TemporaryDirectory() as tmp:
        orchestrator = create_orchestrator(str(Path(tmp) / "test_events.db"))
        assistant = VoiceAssistant(orchestrator, on_event=events.append)
        yield assistant, events


def test_on_event_none_by_default_is_a_noop():
    """Passing no on_event must not change any existing behavior --
    _emit() should just silently do nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        orchestrator = create_orchestrator(str(Path(tmp) / "test.db"))
        assistant = VoiceAssistant(orchestrator)
        assistant._emit("anything", text="should not raise")  # must not error


def test_emit_calls_callback_with_correct_shape(assistant_with_events):
    assistant, events = assistant_with_events
    assistant._emit("wake_detected")
    assistant._emit("transcript", text="hello")
    assert events == [
        {"type": "wake_detected"},
        {"type": "transcript", "text": "hello"},
    ]


def test_pending_task_set_after_destructive_utterance(assistant_with_events):
    """This is the state run() actually checks to decide between emitting
    'confirmation_required' vs 'response' -- verifying it's set correctly
    is what makes that decision in run() trustworthy without needing to
    run() itself (which needs real audio hardware)."""
    assistant, _events = assistant_with_events
    assert assistant._pending_task is None
    assistant.handle_utterance("delete everything")
    assert assistant._pending_task is not None


def test_pending_task_cleared_after_confirmation_resolved(assistant_with_events):
    assistant, _events = assistant_with_events
    assistant.handle_utterance("delete everything")
    assert assistant._pending_task is not None
    assistant.handle_utterance("no, cancel")
    assert assistant._pending_task is None


def test_pending_task_not_set_for_safe_utterance(assistant_with_events):
    assistant, _events = assistant_with_events
    assistant.handle_utterance("remember that I like tea")
    assert assistant._pending_task is None
