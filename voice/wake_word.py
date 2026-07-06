"""
Wake-word detection via openWakeWord.

IMPORTANT — see voice/config.py's top comment: the default model
("hey_jarvis") is a stand-in for "Hey SARVOS", since openWakeWord's
pretrained models don't include that phrase and training a real custom one
is a separate, much larger effort. This is documented here again because
it's the single most important thing to understand about this module
before using it — it is completely real, working wake-word detection, just
not for the exact phrase "Hey SARVOS" yet.

Runs on a background thread so the caller (voice/assistant.py) can do other
things (like speak a response) without blocking the audio input stream.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from voice import config
from voice.audio_io import mic_frame_stream


def _silent_probe_frame():
    import numpy as np

    return np.zeros(1280, dtype=np.int16)


class WakeWordDetector:
    def __init__(
        self,
        model_name: str = config.WAKE_WORD_MODEL,
        threshold: float = config.WAKE_WORD_THRESHOLD,
    ):
        self.model_name = model_name
        self.threshold = threshold
        self._model = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _ensure_loaded(self):
        if self._model is None:
            import openwakeword
            from openwakeword.model import Model

            # Newer openwakeword releases (>=0.5.0, what a fresh `pip
            # install` resolves today) don't bundle the .onnx model files
            # in the package at all -- they're fetched on first use via
            # download_models(), which is idempotent (skips files that
            # already exist). Without this call, Model() fails with
            # "File doesn't exist" for every model, which is exactly what
            # happened during real-Windows testing for this project.
            # Confirmed against the actual openwakeword source
            # (openwakeword/utils.py's download_models function) rather
            # than assumed.
            #
            # The 0.4.x release this sandbox has installed predates that
            # function entirely (bundles files directly), so this is
            # guarded for both cases.
            try:
                openwakeword.utils.download_models()
            except AttributeError:
                pass

            # inference_framework="onnx" avoids a separate failure mode:
            # some releases try tflite_runtime first regardless of
            # platform, which isn't installed and doesn't reliably have
            # Windows wheels at all. 0.4.x doesn't accept this kwarg
            # (TypeError) and only ever used onnx anyway.
            try:
                self._model = Model(inference_framework="onnx")
            except TypeError:
                self._model = Model()

            # Fail loudly here rather than silently: predictions.get(name,
            # 0.0) in listen() would otherwise return 0.0 forever for a
            # mistyped/invalid model name, and the detector would just
            # never trigger with no explanation why.
            probe = self._model.predict(_silent_probe_frame())
            if self.model_name not in probe:
                raise ValueError(
                    f"Unknown wake word model '{self.model_name}'. "
                    f"Available: {sorted(probe.keys())}"
                )
        return self._model

    def listen(self, on_wake: Callable[[], None]) -> None:
        """Blocks the calling thread, continuously processing microphone
        audio and calling on_wake() each time the wake word is detected.
        Call stop() from another thread to end listening.

        IMPORTANT: the wake-word microphone stream is explicitly closed
        (via frame_gen.close(), not just breaking the for-loop) before
        on_wake() runs, and only reopened fresh afterward. Originally this
        stream was left open for on_wake()'s ENTIRE duration -- including
        while the conversation loop opened its own separate microphone
        streams for recording (record_utterance, quick_listen_check).
        Two concurrent input streams on the same physical microphone left
        the wake-word stream in a bad state on real Windows testing: wake
        word detection silently stopped working after a conversation
        ended, with no crash or error, until the whole process was
        restarted. Merely breaking a for-loop over a generator does NOT
        close it in Python (the generator, and the `with sd.InputStream()`
        block inside it, stay alive until explicitly closed or garbage
        collected) -- that gap is exactly what caused this."""
        model = self._ensure_loaded()
        self._stop_event.clear()

        while not self._stop_event.is_set():
            frame_gen = mic_frame_stream(config.SAMPLE_RATE)
            detected = False
            try:
                for frame in frame_gen:
                    if self._stop_event.is_set():
                        break
                    predictions = model.predict(frame)
                    score = predictions.get(self.model_name, 0.0)
                    if score > self.threshold:
                        detected = True
                        break
            finally:
                # Forces mic_frame_stream's `with sd.InputStream()` block
                # to actually exit and release the microphone NOW, not
                # whenever Python happens to garbage-collect the
                # generator later.
                frame_gen.close()

            if self._stop_event.is_set():
                break

            if detected:
                model.reset()  # avoid the tail end of "Hey Jarvis" immediately re-triggering
                try:
                    on_wake()
                except Exception as e:
                    # Defense in depth, one more layer beyond whatever
                    # on_wake() does internally: a crash found during live
                    # testing (pyttsx3's "run loop already started")
                    # previously propagated all the way up through here
                    # and killed the ENTIRE wake-word listening loop --
                    # meaning "Hey Jarvis" stopped working AT ALL until the
                    # whole app was restarted. This loop's only job is to
                    # keep listening indefinitely; nothing on_wake() does
                    # should be able to end that.
                    print(f"[wake_word] on_wake() failed, still listening: {e}")

    def listen_in_background(self, on_wake: Callable[[], None]) -> threading.Thread:
        """Starts `listen` on a daemon thread and returns it."""
        self._thread = threading.Thread(
            target=self.listen, args=(on_wake,), daemon=True
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop_event.set()
