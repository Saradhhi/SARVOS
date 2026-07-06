"""
Regression tests for the actual observed failure mode: a crash during
speak_response killed the ENTIRE background voice pipeline thread,
meaning "Hey Jarvis" stopped responding at all until the whole app was
restarted -- this looked like "stuck," not "crashed," from the outside.

These test the wake-word listen loop's resilience specifically: if
on_wake() raises, the outer listen() loop must survive and keep
listening for the next wake word.
"""

from __future__ import annotations

import threading
import time

from voice.wake_word import WakeWordDetector


def test_on_wake_exception_does_not_kill_the_listen_loop(monkeypatch):
    """This is the direct regression test: on_wake() raises (simulating
    the real pyttsx3 crash found in live testing), and listen() must
    survive it and continue looping rather than propagating the
    exception out and ending the whole background thread."""
    detector = WakeWordDetector(model_name="hey_jarvis")

    call_count = {"n": 0}

    def failing_on_wake():
        call_count["n"] += 1
        if call_count["n"] >= 2:
            # Stop the detector after proving the loop survived one
            # crash and detected again -- otherwise listen() would loop
            # forever waiting for a 3rd detection that will never come.
            detector._stop_event.set()
        raise RuntimeError("simulated crash, e.g. pyttsx3 run loop already started")

    # Fake the model-loading/prediction layer so this test doesn't need
    # real audio hardware -- it's testing listen()'s exception handling
    # around on_wake(), not wake-word detection accuracy itself (already
    # covered by tests/test_wake_word.py).
    class _FakeModel:
        def __init__(self):
            self.calls = 0

        def predict(self, frame):
            self.calls += 1
            # Trigger "detected" on the 2nd and 4th calls, so on_wake()
            # gets called twice -- proving the loop survives the first
            # crash and detects again afterward.
            score = 0.9 if self.calls in (2, 4) else 0.0
            return {"hey_jarvis": score}

        def reset(self):
            pass

    fake_model = _FakeModel()
    monkeypatch.setattr(detector, "_ensure_loaded", lambda: fake_model)

    def fake_frame_stream(sample_rate):
        for _ in range(5):
            if detector._stop_event.is_set():
                return
            yield object()  # content doesn't matter, _FakeModel ignores it

    monkeypatch.setattr("voice.wake_word.mic_frame_stream", fake_frame_stream)

    detector.listen(failing_on_wake)

    assert call_count["n"] == 2, (
        "on_wake should have been called twice -- once per detection -- "
        "proving the first exception didn't end the listen loop"
    )
