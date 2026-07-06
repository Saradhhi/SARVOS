from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from voice.stt import SpeechToText


def _fake_segment(text: str, no_speech_prob: float):
    """Mimics faster_whisper.transcribe.Segment's relevant fields."""
    return SimpleNamespace(text=text, no_speech_prob=no_speech_prob)


def test_high_no_speech_prob_segment_is_filtered_out(monkeypatch):
    """Regression test for a real observed bug: Whisper hallucinated 'You'
    right after a TTS prompt when nothing had actually been said yet.
    A segment with high no_speech_prob should be discarded."""
    stt = SpeechToText()
    fake_model = SimpleNamespace(
        transcribe=lambda audio, language, beam_size: (
            [_fake_segment("You", no_speech_prob=0.85)], None
        )
    )
    stt._model = fake_model
    # Non-trivial audio so the silence short-circuit doesn't skip transcribe.
    audio = np.random.uniform(-0.01, 0.01, 16000).astype(np.float32)
    result = stt.transcribe(audio)
    assert result == ""


def test_low_no_speech_prob_segment_is_kept(monkeypatch):
    stt = SpeechToText()
    fake_model = SimpleNamespace(
        transcribe=lambda audio, language, beam_size: (
            [_fake_segment("What is the weather today", no_speech_prob=0.05)], None
        )
    )
    stt._model = fake_model
    audio = np.random.uniform(-0.01, 0.01, 16000).astype(np.float32)
    result = stt.transcribe(audio)
    assert result == "What is the weather today"


def test_mixed_segments_keeps_only_real_speech(monkeypatch):
    stt = SpeechToText()
    fake_model = SimpleNamespace(
        transcribe=lambda audio, language, beam_size: (
            [
                _fake_segment("Thanks for watching", no_speech_prob=0.9),
                _fake_segment("what's the weather", no_speech_prob=0.02),
            ],
            None,
        )
    )
    stt._model = fake_model
    audio = np.random.uniform(-0.01, 0.01, 16000).astype(np.float32)
    result = stt.transcribe(audio)
    assert result == "what's the weather"
