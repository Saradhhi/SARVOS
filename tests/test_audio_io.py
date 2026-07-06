"""
_should_stop_recording is the pure decision logic behind record_utterance,
extracted specifically so it can be tested with synthetic chunk sequences.
record_utterance itself (the sd.InputStream part) needs a real microphone
and can't be exercised here.
"""

from __future__ import annotations

import numpy as np

from voice.audio_io import _RecordingState, _should_stop_recording

RMS_THRESHOLD = 0.02


def silent_chunk(size=1600):
    return np.zeros(size, dtype=np.float32)


def loud_chunk(size=1600):
    return np.full(size, 0.5, dtype=np.float32)


def test_never_stops_while_speech_continues():
    state = _RecordingState()
    for _ in range(50):  # 5 seconds of continuous "speech"
        stopped = _should_stop_recording(
            loud_chunk(), state, RMS_THRESHOLD,
            silence_chunks_needed=12, max_wait_for_speech_chunks=None,
        )
        assert not stopped
    assert state.speech_started


def test_stops_after_silence_following_speech():
    state = _RecordingState()
    silence_chunks_needed = 12  # 1.2s at 100ms chunks

    # Speak for a bit.
    for _ in range(5):
        _should_stop_recording(
            loud_chunk(), state, RMS_THRESHOLD, silence_chunks_needed, None
        )
    assert state.speech_started

    # Then go quiet -- should NOT stop until silence_chunks_needed reached.
    stopped = False
    for i in range(silence_chunks_needed):
        stopped = _should_stop_recording(
            silent_chunk(), state, RMS_THRESHOLD, silence_chunks_needed, None
        )
        if i < silence_chunks_needed - 1:
            assert not stopped, f"stopped too early at chunk {i}"
    assert stopped, "should have stopped once silence_chunks_needed was reached"


def test_speech_resets_silence_counter():
    """A brief pause mid-sentence shouldn't end the recording -- only
    CONSECUTIVE silence should count."""
    state = _RecordingState()
    silence_chunks_needed = 12

    _should_stop_recording(loud_chunk(), state, RMS_THRESHOLD, silence_chunks_needed, None)
    # A short pause (fewer chunks than needed to trigger stop)...
    for _ in range(5):
        stopped = _should_stop_recording(
            silent_chunk(), state, RMS_THRESHOLD, silence_chunks_needed, None
        )
        assert not stopped
    # ...then speech resumes, which must reset the silence counter.
    _should_stop_recording(loud_chunk(), state, RMS_THRESHOLD, silence_chunks_needed, None)
    assert state.consecutive_silence_chunks == 0


def test_gives_up_if_speech_never_starts_within_wait_window():
    """This is the fix for the 'have to say the wake word every turn'
    problem: a follow-up recording with max_wait_for_speech_chunks set
    should give up (stop, with speech_started still False) if nothing is
    said within that window, rather than waiting the full max_duration."""
    state = _RecordingState()
    max_wait_chunks = 5

    stopped = False
    for i in range(max_wait_chunks + 2):
        stopped = _should_stop_recording(
            silent_chunk(), state, RMS_THRESHOLD,
            silence_chunks_needed=12, max_wait_for_speech_chunks=max_wait_chunks,
        )
        if stopped:
            break
    assert stopped
    assert not state.speech_started
    assert state.chunks_seen == max_wait_chunks


def test_does_not_give_up_early_if_max_wait_not_set():
    """Without max_wait_for_speech_chunks (the normal after-wake-word
    recording, not a follow-up), silence alone should never stop
    recording -- only silence AFTER speech does."""
    state = _RecordingState()
    for _ in range(100):  # far more than any follow-up timeout would allow
        stopped = _should_stop_recording(
            silent_chunk(), state, RMS_THRESHOLD,
            silence_chunks_needed=12, max_wait_for_speech_chunks=None,
        )
        assert not stopped
