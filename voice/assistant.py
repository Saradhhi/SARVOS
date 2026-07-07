"""
VoiceAssistant ties together wake word -> record -> transcribe -> the SAME
Orchestrator used by the CLI/web UI -> speak, using core/factory.py so
voice doesn't diverge from how CLI/web already work.

Deliberately split into two parts:

- `handle_utterance(text)` — pure conversation logic: given transcribed
  text, returns what should be spoken back. This is fully testable without
  any audio hardware (see tests/test_voice_assistant.py), because it's the
  same kind of text-in/text-out logic already covered for CLI/API.
- `run()` — the real audio hardware loop (wake word listener, mic
  recording, TTS playback). This CANNOT be verified in an environment
  without a microphone/speaker — it needs to be run on a real machine.

Confirmation handling mirrors the CLI/web pattern: a destructive action
triggers a spoken prompt, and the NEXT utterance is interpreted as yes/no
rather than routed through the planner again.

Optional on_event callback: lets a caller (e.g. api/server.py's WebSocket
broadcaster) observe pipeline state in real time -- wake word detected,
listening, transcript, thinking, response, speaking start/end,
confirmation required. Purely additive: passing None (the default) means
zero behavior change from before this existed, and run() still works
standalone via `python -m voice.assistant` exactly as it did.
"""

from __future__ import annotations

import uuid
from typing import Callable

from core.factory import create_orchestrator
from core.orchestrator import Orchestrator, PendingConfirmation
from core.schemas import Task

YES_WORDS = {"yes", "yeah", "yep", "yup", "proceed", "confirm", "go ahead", "do it"}
NO_WORDS = {"no", "nope", "cancel", "stop", "don't", "do not"}

# Recognized as a request to go quiet/idle immediately, rather than being
# treated as a real question -- checked against the WHOLE utterance (see
# is_stop_command below), not as a substring, so a genuine question like
# "how do I stop a car" is never mistaken for a cancel command just
# because it contains the word "stop".
STOP_PHRASES = {
    "stop", "stop it", "never mind", "nevermind", "cancel", "cancel that",
    "that's enough", "that is enough", "quiet", "be quiet", "forget it",
    "nothing", "nothing else", "nothing more",
}


def is_stop_command(text: str) -> bool:
    """True if text is (very close to) one of the recognized stop
    phrases in its entirety -- deliberately NOT a substring check. Found
    necessary from real use: interrupting a response and then continuing
    with a real follow-up question worked fine, but there was no way to
    just say 'stop' or 'never mind' and have SARVOS actually go quiet
    instead of waiting for a follow-up question that was never coming."""
    cleaned = text.strip().lower().rstrip(".!?")
    return cleaned in STOP_PHRASES


class VoiceAssistant:
    def __init__(
        self,
        orchestrator: Orchestrator | None = None,
        on_event: Callable[[dict], None] | None = None,
    ):
        self.orchestrator = orchestrator or create_orchestrator()
        self._pending_task: Task | None = None
        self._pending_request_id: str | None = None
        self.on_event = on_event

    def _emit(self, event_type: str, **kwargs) -> None:
        if self.on_event is not None:
            self.on_event({"type": event_type, **kwargs})

    def handle_utterance(self, text: str) -> str:
        """Given transcribed speech, returns the text SARVOS should speak
        back. No audio I/O happens in this method — pure conversation
        logic, fully unit-testable."""
        text = text.strip()
        if not text:
            return "Sorry, I didn't catch that."

        if self._pending_task is not None:
            return self._resolve_pending_confirmation(text)

        request_id = str(uuid.uuid4())
        try:
            results = self.orchestrator.handle_user_message(
                text, request_id, initial_context={"spoken": True}
            )
            return self._results_to_speech(results)
        except PendingConfirmation as pending:
            self._pending_task = pending.task
            self._pending_request_id = request_id
            return f"{pending.prompt} Say yes to proceed, or no to cancel."

    def _resolve_pending_confirmation(self, text: str) -> str:
        lowered = text.lower()
        approved = any(w in lowered for w in YES_WORDS)
        rejected = any(w in lowered for w in NO_WORDS)

        if not approved and not rejected:
            return "Sorry, please say yes to proceed, or no to cancel."

        task = self._pending_task
        request_id = self._pending_request_id
        self._pending_task = None
        self._pending_request_id = None

        try:
            results = self.orchestrator.resume_with_confirmation(
                task, approved, request_id
            )
            return self._results_to_speech(results)
        except PendingConfirmation as pending:
            # A follow-up task also needs confirmation.
            self._pending_task = pending.task
            self._pending_request_id = request_id
            return f"{pending.prompt} Say yes to proceed, or no to cancel."

    def _results_to_speech(self, results) -> str:
        outputs = [r.output for r in results if r.output and not r.new_tasks]
        return " ".join(outputs) if outputs else "Done."

    def run(self) -> None:
        """The real audio hardware loop. Cannot be exercised in an
        environment without a microphone/speaker — see module docstring."""
        from voice import config
        from voice.wake_word import WakeWordDetector
        from voice.stt import SpeechToText
        from voice.tts import TextToSpeech
        from voice.audio_io import record_utterance, ContinuousMicMonitor
        import time

        detector = WakeWordDetector()
        stt = SpeechToText()
        tts = TextToSpeech()

        print(f"Listening for wake word '{detector.model_name}'... (Ctrl+C to stop)")

        def speak_response(response: str) -> bool:
            """Speaks the FULL response while continuously monitoring the
            microphone in real time -- this is genuine mid-speech
            interruption (not just checking between sentences, the
            earlier approach). Returns True if interrupted.

            Uses a dedicated, higher RMS threshold
            (config.BARGE_IN_RMS_THRESHOLD) than normal speech detection,
            specifically to reduce false triggers from SARVOS hearing its
            own voice -- there's no acoustic echo cancellation in this
            build. This reduces, but doesn't eliminate, that problem; a
            headset gives much more reliable results than relying on the
            threshold alone. See ContinuousMicMonitor's docstring in
            voice/audio_io.py for the full explanation."""
            self._emit("speaking_start", text=response)

            monitor = ContinuousMicMonitor()
            monitor.start()
            try:
                def stop_check() -> bool:
                    return monitor.is_loud_enough(config.BARGE_IN_RMS_THRESHOLD)

                interrupted = tts.speak_interruptible(response, stop_check=stop_check)
            finally:
                # MUST fully stop before anything else touches the
                # microphone (record_utterance next) -- same
                # device-contention class of bug found and fixed for the
                # wake-word detector earlier in this project.
                monitor.stop()

            if interrupted:
                self._emit("speaking_interrupted")
            else:
                self._emit("speaking_end")
            return interrupted

        def on_wake():
            print(f"[wake word detected: '{detector.model_name}']")
            self._emit("wake_detected")
            tts.speak("Yes?")
            time.sleep(config.POST_SPEECH_DRAIN_SECONDS)
            conversation_loop(wait_for_speech=None)

        def conversation_loop(wait_for_speech: float | None) -> None:
            """wait_for_speech=None means 'be patient, wait indefinitely
            up to max_duration_s' -- used right after the wake word, and
            right after an interruption (since we already know the person
            is mid-speech in that case). A numeric value means 'give up
            after this many seconds of silence', used for ordinary
            follow-up turns."""
            while True:
                self._emit("listening")
                audio = record_utterance(max_wait_for_speech_s=wait_for_speech)
                text = stt.transcribe(audio)
                if not text:
                    print(f"[no follow-up -- back to listening for "
                          f"'{detector.model_name}']")
                    self._emit("idle")
                    return

                print(f"you (voice)> {text}")
                self._emit("transcript", text=text)

                # Checked BEFORE handle_utterance, and only when nothing is
                # pending confirmation -- "stop" is ALSO a valid "no" answer
                # to an existing confirmation (handle_utterance's own
                # NO_WORDS handling), and that must keep working exactly as
                # before. This is a separate, new behavior: saying "stop"
                # or "never mind" with no pending confirmation now ends the
                # conversation immediately instead of waiting for a
                # follow-up question that was never coming -- found
                # missing from real use (interrupting a response worked,
                # but there was no way to just say "stop" and have it
                # actually go quiet).
                if self._pending_task is None and is_stop_command(text):
                    print("[stop command recognized -- going idle]")
                    self._emit("idle")
                    return

                try:
                    self._emit("thinking")
                    response = self.handle_utterance(text)
                    print(f"sarvos (voice)> {response}")

                    # self._pending_task is set by handle_utterance itself
                    # when this turn resulted in a NEW confirmation request
                    # -- reused here rather than changing handle_utterance's
                    # return contract, since that state already exists for
                    # exactly this purpose (see handle_utterance/​
                    # _resolve_pending_confirmation above).
                    if self._pending_task is not None:
                        self._emit("confirmation_required", prompt=response)
                    else:
                        self._emit("response", text=response)

                    interrupted = speak_response(response)
                except Exception as e:
                    # Defense in depth: a real crash (a pyttsx3
                    # "run loop already started" RuntimeError was found
                    # during live testing here) previously propagated all
                    # the way up and killed this ENTIRE background thread
                    # -- meaning wake-word detection stopped responding
                    # AT ALL until the whole app was restarted, which
                    # looked like "stuck," not "crashed," from the
                    # outside. One bad turn should end that turn, not the
                    # whole voice pipeline.
                    print(f"[voice pipeline] Turn failed, recovering: {e}")
                    self._emit("idle")
                    return

                if interrupted:
                    print("[interrupted -- listening to what you're saying now]")
                    wait_for_speech = None
                else:
                    print(f"[conversation mode -- no wake word needed for "
                          f"{config.FOLLOWUP_TIMEOUT_SECONDS:.0f}s]")
                    wait_for_speech = config.FOLLOWUP_TIMEOUT_SECONDS

        detector.listen(on_wake)


if __name__ == "__main__":
    VoiceAssistant().run()
