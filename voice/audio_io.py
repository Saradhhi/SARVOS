"""
Microphone I/O via sounddevice (PortAudio bindings).

Three responsibilities:
1. `mic_frame_stream`: a generator of small, fixed-size raw frames for the
   wake-word detector, which needs continuous short chunks (openWakeWord
   expects 80ms @ 16kHz = 1280 samples per frame).
2. `record_utterance`: records a full spoken phrase, using simple
   RMS-energy silence detection to know when the person has stopped
   talking, rather than a fixed duration.
3. `_should_stop_recording`: the pure decision logic extracted out of
   record_utterance so it can be unit-tested with synthetic chunk
   sequences, without needing a real microphone. record_utterance itself
   (the sd.InputStream part) can't be tested without real audio hardware —
   see tests/test_audio_io.py for what IS covered.

This is a real, working energy-based VAD (voice activity detection) —
not a trained model like Silero VAD, just an RMS threshold. That's a
deliberate, documented scope cut (see config.py's SPEECH_RMS_THRESHOLD
comment): good enough for a quiet room, not robust against noisy
environments.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterator

import numpy as np
import sounddevice as sd

from voice import config

WAKE_WORD_FRAME_SAMPLES = 1280  # 80ms @ 16kHz, what openWakeWord expects
CHUNK_DURATION_S = 0.1  # 100ms analysis chunks for silence detection


def mic_frame_stream(sample_rate: int = config.SAMPLE_RATE) -> Iterator[np.ndarray]:
    """Yields int16 mono frames of WAKE_WORD_FRAME_SAMPLES forever, until
    the generator is closed. Caller (WakeWordDetector) is responsible for
    stopping iteration."""
    with sd.InputStream(
        samplerate=sample_rate, channels=1, dtype="int16",
        blocksize=WAKE_WORD_FRAME_SAMPLES,
    ) as stream:
        while True:
            frame, _overflowed = stream.read(WAKE_WORD_FRAME_SAMPLES)
            yield frame.flatten()


@dataclass
class _RecordingState:
    speech_started: bool = False
    consecutive_silence_chunks: int = 0
    chunks_seen: int = 0


def _should_stop_recording(
    chunk: np.ndarray,
    state: _RecordingState,
    speech_rms_threshold: float,
    silence_chunks_needed: int,
    max_wait_for_speech_chunks: int | None,
) -> bool:
    """Pure decision logic: given one new chunk and the running state,
    should recording stop now? Mutates state in place (chunks_seen,
    speech_started, consecutive_silence_chunks) and returns True/False.

    Extracted from record_utterance specifically so this can be unit
    tested with a synthetic sequence of chunks — no microphone needed."""
    state.chunks_seen += 1
    rms = float(np.sqrt(np.mean(np.square(chunk))))
    is_speech = rms > speech_rms_threshold

    if is_speech:
        state.speech_started = True
        state.consecutive_silence_chunks = 0
        return False

    if state.speech_started:
        state.consecutive_silence_chunks += 1
        return state.consecutive_silence_chunks >= silence_chunks_needed

    # Speech never started yet -- have we waited long enough to give up?
    if max_wait_for_speech_chunks is not None:
        return state.chunks_seen >= max_wait_for_speech_chunks

    return False


class ContinuousMicMonitor:
    """Runs a background thread continuously sampling the microphone and
    tracking the most recent RMS energy reading, so a caller can check
    "is the person talking RIGHT NOW" at any moment -- this is what makes
    real mid-speech interruption possible (checking only between
    sentences, the earlier approach, isn't true barge-in).

    IMPORTANT -- same class of bug as WakeWordDetector's stream-contention
    issue (found and fixed earlier in this project): this class opens its
    own microphone InputStream, which must NOT be open at the same time as
    another one (the wake-word detector's stream, or record_utterance's).
    Always call stop() -- which fully closes the stream, not just signals
    an intent to stop -- before starting any other microphone access.

    Uses a HIGHER RMS threshold than normal speech detection (see
    config.BARGE_IN_RMS_THRESHOLD) specifically to reduce false triggers
    from SARVOS hearing its own voice -- there's no acoustic echo
    cancellation in this build. This reduces, but does not eliminate,
    self-interruption; a headset (mic physically separated from the
    speaker output) remains the reliable fix if false interruptions are
    still frequent.
    """

    def __init__(
        self,
        sample_rate: int = config.SAMPLE_RATE,
        chunk_duration_s: float = 0.1,
    ):
        self.sample_rate = sample_rate
        self.chunk_samples = int(sample_rate * chunk_duration_s)
        self._current_rms = 0.0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            with sd.InputStream(
                samplerate=self.sample_rate, channels=1, dtype="float32",
                blocksize=self.chunk_samples,
            ) as stream:
                while not self._stop_event.is_set():
                    chunk, _overflowed = stream.read(self.chunk_samples)
                    rms = float(np.sqrt(np.mean(np.square(chunk.flatten()))))
                    with self._lock:
                        self._current_rms = rms
        except Exception as e:
            print(f"[audio] Continuous mic monitor stopped: {e}")

    def stop(self) -> None:
        """Fully stops AND closes the microphone stream -- always call
        this before any other code tries to open the microphone again."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def current_rms(self) -> float:
        with self._lock:
            return self._current_rms

    def is_loud_enough(self, threshold: float) -> bool:
        return self.current_rms() > threshold


def record_utterance(
    sample_rate: int = config.SAMPLE_RATE,
    silence_duration_s: float = config.SILENCE_DURATION_SECONDS,
    max_duration_s: float = config.MAX_UTTERANCE_SECONDS,
    speech_rms_threshold: float = config.SPEECH_RMS_THRESHOLD,
    max_wait_for_speech_s: float | None = None,
) -> np.ndarray:
    """Records from the default microphone until the person stops talking
    (silence_duration_s of quiet after speech was heard), max_duration_s is
    reached, or — if max_wait_for_speech_s is given — the person never
    starts speaking at all within that window (used for the follow-up
    conversation window, so SARVOS doesn't sit there listening for the
    full max_duration_s if nothing was said). Returns float32 samples in
    [-1, 1], the format voice/stt.py's SpeechToText.transcribe expects."""
    max_chunks = int(max_duration_s / CHUNK_DURATION_S)
    silence_chunks_needed = int(silence_duration_s / CHUNK_DURATION_S)
    max_wait_chunks = (
        int(max_wait_for_speech_s / CHUNK_DURATION_S)
        if max_wait_for_speech_s is not None
        else None
    )
    chunk_samples = int(sample_rate * CHUNK_DURATION_S)

    frames: list[np.ndarray] = []
    state = _RecordingState()

    with sd.InputStream(
        samplerate=sample_rate, channels=1, dtype="float32", blocksize=chunk_samples,
    ) as stream:
        for chunk_index in range(max_chunks):
            chunk, _overflowed = stream.read(chunk_samples)
            chunk = chunk.flatten()
            frames.append(chunk)

            if config.DEBUG_AUDIO and chunk_index % 10 == 0:  # ~every 1s
                rms = float(np.sqrt(np.mean(np.square(chunk))))
                print(f"[audio debug] chunk {chunk_index}: rms={rms:.5f} "
                      f"threshold={speech_rms_threshold:.5f} "
                      f"speech_started={state.speech_started}")

            if _should_stop_recording(
                chunk, state, speech_rms_threshold, silence_chunks_needed, max_wait_chunks
            ):
                if config.DEBUG_AUDIO:
                    print(f"[audio debug] stopped at chunk {chunk_index}, "
                          f"speech_started={state.speech_started}")
                break

    if config.DEBUG_AUDIO:
        print(f"[audio debug] final: frames_captured={len(frames)}, "
              f"speech_started={state.speech_started}, "
              f"chunks_seen={state.chunks_seen}/{max_chunks}")

    if not frames or not state.speech_started:
        # Either literally nothing was captured, or the person never
        # spoke within the wait window -- both mean "no utterance."
        return np.array([], dtype=np.float32)
    return np.concatenate(frames)


def quick_listen_check(
    duration_s: float,
    sample_rate: int = config.SAMPLE_RATE,
    speech_rms_threshold: float = config.SPEECH_RMS_THRESHOLD,
) -> bool:
    """Briefly samples the microphone for duration_s seconds and returns
    True if speech-level energy was detected. Used BETWEEN sentences of a
    spoken response (while SARVOS is silent, not while it's talking) to
    check whether the person is trying to interrupt -- deliberately NOT
    used while TTS audio is actively playing, since without echo
    cancellation that would very likely trigger on SARVOS's own voice
    bleeding into the mic rather than the person actually speaking."""
    chunk_samples = int(sample_rate * CHUNK_DURATION_S)
    num_chunks = max(1, int(duration_s / CHUNK_DURATION_S))

    with sd.InputStream(
        samplerate=sample_rate, channels=1, dtype="float32", blocksize=chunk_samples,
    ) as stream:
        for _ in range(num_chunks):
            chunk, _overflowed = stream.read(chunk_samples)
            rms = float(np.sqrt(np.mean(np.square(chunk.flatten()))))
            if rms > speech_rms_threshold:
                return True
    return False
