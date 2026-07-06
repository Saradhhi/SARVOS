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
"""

from __future__ import annotations

import uuid

from core.factory import create_orchestrator
from core.orchestrator import Orchestrator, PendingConfirmation
from core.schemas import Task

YES_WORDS = {"yes", "yeah", "yep", "yup", "proceed", "confirm", "go ahead", "do it"}
NO_WORDS = {"no", "nope", "cancel", "stop", "don't", "do not"}


class VoiceAssistant:
    def __init__(self, orchestrator: Orchestrator | None = None):
        self.orchestrator = orchestrator or create_orchestrator()
        self._pending_task: Task | None = None
        self._pending_request_id: str | None = None

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
        from voice.audio_io import record_utterance, quick_listen_check
        from voice.text_utils import split_into_sentences
        import time

        detector = WakeWordDetector()
        stt = SpeechToText()
        tts = TextToSpeech()

        print(f"Listening for wake word '{detector.model_name}'... (Ctrl+C to stop)")

        def speak_response(response: str) -> bool:
            """Speaks one sentence at a time, checking BETWEEN sentences
            (while SARVOS is silent) for interruption. Returns True if
            interrupted -- i.e. the person started talking before SARVOS
            finished, so remaining sentences are skipped and control
            returns immediately to listening.

            This is deliberately NOT continuous "listen while speaking"
            barge-in: without a headset (no echo/acoustic cancellation),
            monitoring the mic WHILE audio is actively playing risks
            picking up SARVOS's own voice as if it were the person
            interrupting -- this was observed directly during real-machine
            testing (a garbled transcription of SARVOS's own tail-end
            speech, followed by SARVOS confusingly responding to itself).
            Checking only in the gaps between sentences is a real,
            testable middle ground, at the cost of not being able to
            interrupt mid-sentence, only between sentences."""
            sentences = split_into_sentences(response)
            for sentence in sentences:
                tts.speak(sentence)
                time.sleep(config.POST_SPEECH_DRAIN_SECONDS)
                if quick_listen_check(config.INTERRUPT_CHECK_SECONDS):
                    return True
            return False

        def on_wake():
            print(f"[wake word detected: '{detector.model_name}']")
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
                audio = record_utterance(max_wait_for_speech_s=wait_for_speech)
                text = stt.transcribe(audio)
                if not text:
                    print(f"[no follow-up -- back to listening for "
                          f"'{detector.model_name}']")
                    return

                print(f"you (voice)> {text}")
                response = self.handle_utterance(text)
                print(f"sarvos (voice)> {response}")

                interrupted = speak_response(response)
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
