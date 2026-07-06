"""
Speech-to-text via faster-whisper (CTranslate2-based reimplementation of
OpenAI's Whisper — meaningfully faster on CPU, same free/offline model
weights). Model download happens once, on first use, cached locally by
the library — no ongoing cost, no API key.

The model is lazy-loaded (not in __init__) so that importing this module,
or constructing a SpeechToText instance, is cheap. That matters for
testing: tests can exercise everything except the actual model load
without needing a multi-hundred-MB download in CI.
"""

from __future__ import annotations

import numpy as np

from voice import config


class SpeechToText:
    def __init__(self, model_size: str = config.WHISPER_MODEL_SIZE):
        self.model_size = model_size
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            # int8 on CPU: the accuracy/speed tradeoff that makes Whisper
            # usable on a normal machine without a GPU.
            self._model = WhisperModel(
                self.model_size, device="cpu", compute_type="int8"
            )
        return self._model

    def transcribe(self, audio: np.ndarray, sample_rate: int = config.SAMPLE_RATE) -> str:
        """audio: float32 mono samples in [-1, 1], as produced by
        voice/audio_io.py's recorder. Returns empty string for silence/
        near-silence rather than raising, since "nothing was said" is a
        normal outcome, not an error."""
        if audio.size == 0 or float(np.abs(audio).mean()) < 1e-4:
            return ""

        model = self._ensure_loaded()
        segments, _info = model.transcribe(audio, language="en", beam_size=5)

        # Whisper models are well known to hallucinate short filler text
        # ("You", "Thank you", "Thanks for watching") from near-silent or
        # noisy audio that isn't actually speech -- observed directly
        # during real-machine testing (a garbled "You" transcription right
        # after a prompt, with nothing actually said yet). Each segment
        # carries no_speech_prob, Whisper's own estimate of "this segment
        # probably isn't speech at all" -- filtering on it is the standard,
        # documented mitigation for this exact failure mode, not a guess.
        kept = [s for s in segments if s.no_speech_prob < config.NO_SPEECH_PROB_THRESHOLD]
        text = " ".join(segment.text.strip() for segment in kept)
        return text.strip()
