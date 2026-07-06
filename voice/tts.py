"""
Text-to-speech via pyttsx3 — chosen over Piper for the initial build because
it uses the OS's built-in voices (SAPI5 on Windows, NSSpeechSynthesizer on
macOS, espeak on Linux) with zero model downloads and zero setup. Piper
gives noticeably better voice quality and is a reasonable future upgrade,
but adds real setup complexity (downloading a platform binary plus voice
model files) that isn't worth taking on for a first working version.

IMPORTANT — a fresh pyttsx3.Engine is created for EVERY call to speak(),
not reused across calls. This looks wasteful but is the standard, widely
reported workaround for a well-documented pyttsx3-on-Windows bug: reusing
one engine instance across multiple say()/runAndWait() cycles frequently
produces complete silence with no error at all (found during real-machine
testing for this project — engine initialized fine, is_available()
reported True, runAndWait() returned normally, but nothing was audible,
matching a pattern reported repeatedly by other pyttsx3 users on Windows).
Recreating the engine per call avoids whatever internal state causes this.

Graceful degradation: if no TTS engine is available on the system at all
(e.g. no espeak installed on Linux), `speak()` becomes a no-op that logs
instead of crashing — matching the same pattern used for Ollama being
unavailable in llm/client.py.
"""

from __future__ import annotations

from voice import config


class TextToSpeech:
    def __init__(self):
        self._available = False
        try:
            import pyttsx3

            # Construct once just to confirm a driver actually exists on
            # this system (cheap availability check) -- the real speak()
            # calls each build their own fresh engine, per the module
            # docstring above.
            probe_engine = pyttsx3.init()
            del probe_engine
            self._available = True
        except Exception as e:
            # Broad except is deliberate: pyttsx3's failure modes vary a
            # lot by platform (missing espeak on Linux, missing SAPI
            # registration on a broken Windows install, etc.) and all of
            # them should land in the same "degrade to text-only" path.
            print(f"[voice/tts] TTS engine unavailable, falling back to "
                  f"text-only: {e}")

    def is_available(self) -> bool:
        return self._available

    def speak(self, text: str) -> None:
        if not self._available:
            print(f"[sarvos, text-only — no TTS engine available] {text}")
            return

        import pyttsx3

        engine = pyttsx3.init()
        try:
            engine.setProperty("rate", config.TTS_RATE_WORDS_PER_MINUTE)
            engine.say(text)
            engine.runAndWait()
        finally:
            engine.stop()
