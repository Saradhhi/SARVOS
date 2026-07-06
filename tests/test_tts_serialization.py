"""
Regression tests for a real crash found during live testing:
RuntimeError: run loop already started, caused by a new pyttsx3 engine's
runAndWait() starting before a previous (interrupted) engine's underlying
driver loop had actually finished tearing down.

These use a FAKE pyttsx3 module (installed into sys.modules) so the
serialization behavior can be tested with controllable timing, without a
real TTS engine -- this sandbox has no espeak/SAPI5 driver at all, so the
real library would just hit the graceful-fallback path and never
exercise the lock-protected code at all.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from voice.tts import TextToSpeech


class _FakeEngine:
    """Mimics the pyttsx3 engine interface used by voice/tts.py, with a
    controllable delay in runAndWait() to simulate slow driver teardown."""

    def __init__(self, run_duration: float = 0.3):
        self.run_duration = run_duration
        self.stopped = False

    def setProperty(self, name, value):
        pass

    def say(self, text):
        pass

    def runAndWait(self):
        # Simulates speech playing for run_duration, checking self.stopped
        # periodically so engine.stop() can cut it short -- like a real
        # TTS engine would.
        elapsed = 0.0
        step = 0.02
        while elapsed < self.run_duration and not self.stopped:
            time.sleep(step)
            elapsed += step

    def stop(self):
        self.stopped = True


@pytest.fixture
def fake_pyttsx3(monkeypatch):
    fake_module = types.SimpleNamespace(init=lambda: _FakeEngine())
    monkeypatch.setitem(sys.modules, "pyttsx3", fake_module)
    return fake_module


@pytest.fixture
def tts(fake_pyttsx3):
    instance = TextToSpeech()
    instance._available = True  # bypass the real availability probe
    return instance


def test_speak_interruptible_completes_normally_without_interruption(tts):
    result = tts.speak_interruptible("hello", stop_check=lambda: False, poll_interval=0.02)
    assert result is False


def test_speak_interruptible_stops_early_when_interrupted(tts):
    call_count = {"n": 0}

    def stop_after_a_few_checks():
        call_count["n"] += 1
        return call_count["n"] >= 2

    result = tts.speak_interruptible(
        "a longer utterance", stop_check=stop_after_a_few_checks, poll_interval=0.02
    )
    assert result is True


def test_lock_serializes_overlapping_speak_calls(tts):
    """This is the actual regression test for the crash: while one
    speak_interruptible call is in progress (holding the lock through a
    full, non-timeout join), a second call attempted from another thread
    must not proceed until the first has COMPLETELY finished -- not just
    'probably finished'. If this lock were timeout-based (the original,
    buggy version), the second call could start while the first engine's
    teardown was still in flight."""
    call_order = []
    lock_check = threading.Event()

    def first_call():
        call_order.append(("first", "start"))
        tts.speak_interruptible("first utterance", stop_check=lambda: False, poll_interval=0.02)
        call_order.append(("first", "end"))
        lock_check.set()

    thread = threading.Thread(target=first_call)
    thread.start()
    time.sleep(0.05)  # let the first call acquire the lock and start its engine

    # The lock must be held right now -- acquiring it should fail quickly.
    acquired = tts._speak_lock.acquire(timeout=0.05)
    if acquired:
        tts._speak_lock.release()
    assert not acquired, "lock should still be held by the in-progress first call"

    thread.join(timeout=2)
    assert call_order == [("first", "start"), ("first", "end")]

    # After the first call fully finishes, the lock must be free again.
    acquired_after = tts._speak_lock.acquire(timeout=0.5)
    assert acquired_after, "lock should be free once the first call has completed"
    tts._speak_lock.release()


def test_speak_and_speak_interruptible_share_the_same_lock(tts):
    """Both methods must serialize against EACH OTHER too, not just
    against themselves -- a plain speak() call in progress must also
    block a concurrent speak_interruptible() call, and vice versa."""
    order = []

    def slow_speak():
        order.append("speak-start")
        tts.speak("blocking utterance")
        order.append("speak-end")

    thread = threading.Thread(target=slow_speak)
    thread.start()
    time.sleep(0.05)

    acquired = tts._speak_lock.acquire(timeout=0.05)
    if acquired:
        tts._speak_lock.release()
    assert not acquired, "speak() should hold the same lock as speak_interruptible()"

    thread.join(timeout=2)
    assert order == ["speak-start", "speak-end"]
