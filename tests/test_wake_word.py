"""
These tests exercise the REAL openwakeword library (not mocked) — actual
model loading and actual inference on a synthetic silent frame. What they
deliberately do NOT test is real microphone capture or a real "Hey Jarvis"
utterance actually triggering detection, since this environment has no
audio hardware. That part needs verification on a real machine.
"""

from __future__ import annotations

import numpy as np
import pytest

from voice.wake_word import WakeWordDetector


def test_valid_model_name_loads_successfully():
    detector = WakeWordDetector(model_name="hey_jarvis")
    model = detector._ensure_loaded()
    assert model is not None


def test_invalid_model_name_fails_loudly_at_load_time():
    """Regression test: predictions.get(name, 0.0) would otherwise return
    0.0 forever for a mistyped model name, silently never triggering with
    no explanation. Found and fixed by actually running the detector
    against the real library rather than assuming the API worked."""
    detector = WakeWordDetector(model_name="not_a_real_wake_word")
    with pytest.raises(ValueError, match="Unknown wake word model"):
        detector._ensure_loaded()


def test_predict_on_silence_scores_near_zero():
    detector = WakeWordDetector(model_name="hey_jarvis")
    model = detector._ensure_loaded()
    silent_frame = np.zeros(1280, dtype=np.int16)
    predictions = model.predict(silent_frame)
    assert predictions["hey_jarvis"] < detector.threshold
