"""
Voice configuration, read from environment. As with llm/config.py, defaults
are chosen so the free/local path works out of the box.

IMPORTANT, READ THIS: openWakeWord (the wake-word engine used here) ships a
fixed set of pretrained models: alexa, hey_jarvis, hey_mycroft, hey_marvin,
timer, weather. It does NOT include "Hey SARVOS" — training a genuinely
custom wake word is a separate ML project (synthetic data generation +
training pipeline), out of scope for this build. The default trigger
phrase is "Hey Jarvis" as the closest thematic stand-in. Change
SARVOS_WAKE_WORD to any of the other bundled options if you prefer.
"""

from __future__ import annotations

import os

WAKE_WORD_MODEL = os.environ.get("SARVOS_WAKE_WORD", "hey_jarvis")
WAKE_WORD_THRESHOLD = float(os.environ.get("SARVOS_WAKE_THRESHOLD", "0.5"))

SAMPLE_RATE = 16000  # required by both openWakeWord and Whisper

# faster-whisper model size. "base" is a reasonable CPU-speed/accuracy
# tradeoff for a personal assistant; "small" or "medium" trade speed for
# accuracy if you have the CPU headroom (SARVOS_WHISPER_MODEL=small).
WHISPER_MODEL_SIZE = os.environ.get("SARVOS_WHISPER_MODEL", "base")

# Segments with no_speech_prob above this are discarded as likely
# hallucinated text from silence/noise rather than real speech (see
# voice/stt.py's SpeechToText.transcribe for why). 0.6 is a starting point,
# not empirically tuned against real recordings -- lower it if real short
# utterances start getting incorrectly discarded, raise it if hallucinated
# filler words still get through.
NO_SPEECH_PROB_THRESHOLD = float(os.environ.get("SARVOS_NO_SPEECH_PROB_THRESHOLD", "0.6"))

# How long a silence has to last (seconds) after speech was detected before
# we consider the utterance finished and stop recording.
SILENCE_DURATION_SECONDS = float(os.environ.get("SARVOS_SILENCE_SECONDS", "1.2"))

# Hard ceiling on a single utterance's length, regardless of silence
# detection, so a noisy environment can't make SARVOS record forever.
MAX_UTTERANCE_SECONDS = float(os.environ.get("SARVOS_MAX_UTTERANCE_SECONDS", "15"))

# After answering, how long to keep listening for a FOLLOW-UP without
# requiring the wake word again -- lets a conversation flow naturally
# instead of demanding "Hey Jarvis" before every single turn. If nothing
# is said within this window, SARVOS goes back to wake-word-only listening.
# Default is 5 minutes, per explicit preference (confirmed working after
# the wake-word-goes-dead-after-a-conversation bug was fixed -- a long
# window like this is exactly the scenario that bug would have broken).
FOLLOWUP_TIMEOUT_SECONDS = float(os.environ.get("SARVOS_FOLLOWUP_TIMEOUT_SECONDS", "300"))

# RMS energy threshold above which audio is considered "speech" rather than
# background noise, for the silence-detection cutoff. This is a simple
# energy-based VAD, not a trained model — good enough for a quiet room,
# not robust in a noisy one. Tune via env var if it cuts off too eagerly
# or not eagerly enough.
SPEECH_RMS_THRESHOLD = float(os.environ.get("SARVOS_SPEECH_RMS_THRESHOLD", "0.02"))

# Threshold for detecting a REAL mid-speech interruption (while TTS is
# actively playing), separate from and higher than SPEECH_RMS_THRESHOLD
# above. Deliberately higher: without acoustic echo cancellation, SARVOS's
# own voice bleeding into the mic would otherwise frequently trigger a
# false "interruption." This reduces, but doesn't eliminate, that problem
# -- if false interruptions are still frequent, a headset (mic physically
# separated from speaker output) is the reliable fix, not a higher
# threshold alone. Tune via env var based on your actual speaker volume
# and mic sensitivity.
BARGE_IN_RMS_THRESHOLD = float(os.environ.get("SARVOS_BARGE_IN_RMS_THRESHOLD", "0.08"))

TTS_RATE_WORDS_PER_MINUTE = int(os.environ.get("SARVOS_TTS_RATE", "175"))

# How long to wait for a TTS engine to finish tearing down after being
# interrupted, before giving up and moving on anyway. Found necessary from
# a real hang during live testing: pyttsx3's engine.stop() doesn't always
# promptly unblock runAndWait() on Windows, especially for longer text --
# an unconditional (infinite) wait here previously froze the entire voice
# pipeline thread permanently, with no crash or error, just silence.
TTS_TEARDOWN_TIMEOUT_SECONDS = float(
    os.environ.get("SARVOS_TTS_TEARDOWN_TIMEOUT_SECONDS", "3")
)

# Brief pause after TTS finishes speaking before the mic starts listening
# again, to let any trailing audio-device buffer drain. Without a headset
# (no echo cancellation), listening again the instant speak() returns risks
# picking up the tail end of SARVOS's own voice as if it were the person
# talking -- this was observed directly during real-machine testing (a
# garbled "You" transcription followed by SARVOS responding to itself).
# A short pause alone won't eliminate self-hearing entirely if the speaker
# and mic are physically close together; a headset is the reliable fix.
POST_SPEECH_DRAIN_SECONDS = float(os.environ.get("SARVOS_POST_SPEECH_DRAIN_SECONDS", "0.4"))

# How long to sample the mic BETWEEN sentences of a spoken response, to
# check whether the person is trying to interrupt. Deliberately short and
# deliberately only used while SARVOS is silent (between sentences), not
# while actively speaking -- see audio_io.quick_listen_check's docstring
# for why continuous barge-in isn't used here.
INTERRUPT_CHECK_SECONDS = float(os.environ.get("SARVOS_INTERRUPT_CHECK_SECONDS", "0.5"))

# Opt-in diagnostic logging: prints periodic RMS readings while waiting for
# speech, so a "SARVOS didn't hear me at all, repeatedly" report can be
# diagnosed with real numbers next time instead of guessed at blind.
# SARVOS_DEBUG_AUDIO=1 to enable.
DEBUG_AUDIO = os.environ.get("SARVOS_DEBUG_AUDIO", "0") == "1"
