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

import threading

from voice import config


class TextToSpeech:
    def __init__(self):
        self._available = False
        # Serializes ALL speak/speak_interruptible calls. This is the real
        # fix for a crash found during live testing: "RuntimeError: run
        # loop already started". pyttsx3's underlying speech-driver loop
        # (SAPI5 on Windows) doesn't always finish tearing down instantly
        # after engine.stop() -- if a SECOND engine's runAndWait() starts
        # too soon after an interrupted first one, pyttsx3's shared
        # internal state collides and raises. A timeout-based join (what
        # this used before) can return before the first engine has truly
        # finished, letting the next call race ahead into exactly that
        # collision. Holding this lock for the ENTIRE call, including a
        # full (non-timeout) join, guarantees the next speak call can't
        # start until the previous one is completely done -- not just
        # "probably done by now."
        self._speak_lock = threading.Lock()
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

        with self._speak_lock:
            engine = pyttsx3.init()
            try:
                engine.setProperty("rate", config.TTS_RATE_WORDS_PER_MINUTE)
                engine.say(text)
                engine.runAndWait()
            finally:
                engine.stop()

    def speak_interruptible(self, text: str, stop_check, poll_interval: float = 0.1) -> bool:
        """Speaks text on a background thread while repeatedly calling
        stop_check() (no args, returns bool) from the calling thread. If
        stop_check() ever returns True, playback is cut off immediately
        via engine.stop() and this returns True (interrupted). Returns
        False if the utterance finished on its own.

        This is what makes REAL mid-speech interruption possible (as
        opposed to only checking between sentences) -- stop_check is
        typically a live microphone-energy check (see
        voice/audio_io.py's ContinuousMicMonitor), polled every
        poll_interval seconds while speech is actively playing."""
        if not self._available:
            print(f"[sarvos, text-only — no TTS engine available] {text}")
            return False

        import pyttsx3

        with self._speak_lock:
            engine = pyttsx3.init()
            engine.setProperty("rate", config.TTS_RATE_WORDS_PER_MINUTE)
            interrupted = {"value": False}

            def _run_speech():
                engine.say(text)
                engine.runAndWait()

            speech_thread = threading.Thread(target=_run_speech, daemon=True)
            speech_thread.start()

            while speech_thread.is_alive():
                if stop_check():
                    interrupted["value"] = True
                    try:
                        engine.stop()
                    except Exception:
                        pass
                    break
                threading.Event().wait(poll_interval)

            # Full join, no timeout: block until the thread has ACTUALLY
            # finished (whether it finished naturally or was stopped),
            # not just "probably finished by now." This is the actual fix
            # -- releasing the lock only once this is genuinely true means
            # the next speak call can never race ahead into the previous
            # engine's still-in-progress teardown.
            speech_thread.join()
            try:
                engine.stop()
            except Exception:
                pass
            return interrupted["value"]
