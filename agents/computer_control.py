"""
ComputerControlAgent -- screenshot, clipboard, volume, brightness, lock,
launch/close applications, shutdown/restart/sleep.

Platform-specific libraries (pycaw for Windows volume, screen_brightness_
control for brightness) are imported LAZILY, inside the methods that use
them, not at module level -- confirmed directly that pycaw's own internal
module fails to IMPORT at all on Linux (ctypes.HRESULT doesn't exist
outside Windows), which would otherwise crash this whole agent's import
on any non-Windows machine. Every platform-specific operation degrades
gracefully with a clear message if its backend isn't available, the same
pattern as System Info's no-battery handling.

The power-state operations (shutdown/restart/sleep) route through a
single small method, _execute_system_command(), specifically so tests can
replace it entirely and prove the confirmation gate works WITHOUT ever
risking a real shutdown during a test run -- unlike AutoDeveloper's
deploy command (safe to actually run in a test), actually restarting or
shutting down a machine during automated testing would be a real
problem, so this is never invoked for real in this project's own tests.
"""

from __future__ import annotations

import platform
import subprocess
import sys

import psutil

from agents import computer_control_config as config
from agents.base import BaseAgent
from agents.computer_control_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task


class ComputerControlAgent(BaseAgent):
    name = AgentName.COMPUTER_CONTROL

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        handlers = {
            Operation.SCREENSHOT: self._screenshot,
            Operation.READ_CLIPBOARD: self._read_clipboard,
            Operation.WRITE_CLIPBOARD: self._write_clipboard,
            Operation.MUTE: lambda t, i: self._set_mute(t, True),
            Operation.UNMUTE: lambda t, i: self._set_mute(t, False),
            Operation.VOLUME_UP: lambda t, i: self._adjust_volume(t, config.VOLUME_STEP),
            Operation.VOLUME_DOWN: lambda t, i: self._adjust_volume(t, -config.VOLUME_STEP),
            Operation.SET_VOLUME: self._set_volume,
            Operation.BRIGHTNESS_UP: lambda t, i: self._adjust_brightness(t, config.BRIGHTNESS_STEP),
            Operation.BRIGHTNESS_DOWN: lambda t, i: self._adjust_brightness(t, -config.BRIGHTNESS_STEP),
            Operation.SET_BRIGHTNESS: self._set_brightness,
            Operation.LOCK: self._lock,
            Operation.LAUNCH_APP: self._launch_app,
            Operation.CLOSE_APP: self._close_app,
            Operation.SHUTDOWN: self._shutdown,
            Operation.RESTART: self._restart,
            Operation.SLEEP: self._sleep,
        }
        handler = handlers.get(intent.operation)
        if handler is None:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"I couldn't work out a computer-control action from: "
                    f"'{task.instruction}'. Try 'take a screenshot', "
                    f"'mute', 'lock my computer', 'launch notepad', or "
                    f"'shut down my computer'."
                ),
            )
        return handler(task, intent)

    # ---- Screenshot / clipboard (SAFE) ---------------------------------

    def _screenshot(self, task: Task, intent) -> AgentResult:
        try:
            from PIL import ImageGrab
            import os
            import time

            os.makedirs(config.SCREENSHOT_DIR, exist_ok=True)
            path = os.path.join(config.SCREENSHOT_DIR, f"screenshot_{int(time.time())}.png")
            img = ImageGrab.grab()
            img.save(path)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Screenshot saved to {path}",
                data={"path": path},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't take a screenshot: {e}",
                error="screenshot_failed",
            )

    def _read_clipboard(self, task: Task, intent) -> AgentResult:
        try:
            import pyperclip
            content = pyperclip.paste()
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Clipboard contains: {content}" if content else "Clipboard is empty.",
                data={"content": content},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't read the clipboard: {e}",
                error="clipboard_failed",
            )

    def _write_clipboard(self, task: Task, intent) -> AgentResult:
        try:
            import pyperclip
            pyperclip.copy(intent.text_arg)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Copied to clipboard: {intent.text_arg}",
                data={"content": intent.text_arg},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't write to the clipboard: {e}",
                error="clipboard_failed",
            )

    # ---- Lock (SAFE) -----------------------------------------------------

    def _lock(self, task: Task, intent) -> AgentResult:
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.LockWorkStation()
                return AgentResult(
                    task_id=task.task_id, agent=self.name, success=True,
                    output="Locked the computer.",
                )
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Locking isn't implemented for this platform ({platform.system()}).",
                error="lock_not_supported",
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't lock the computer: {e}",
                error="lock_failed",
            )

    # ---- Volume (SENSITIVE) -----------------------------------------------

    def _get_volume_interface(self):
        """Lazy import -- pycaw's own module fails to IMPORT at all on
        non-Windows platforms (confirmed directly: ctypes.HRESULT doesn't
        exist outside Windows), so this must never be imported at module
        level.

        Handles BOTH pycaw APIs, old and new -- a real version mismatch
        confirmed from a user's diagnostic on pycaw 1.4.16: newer pycaw's
        GetSpeakers() returns a wrapped AudioDevice object exposing an
        `.EndpointVolume` property, and no longer has the `.Activate()`
        method older pycaw required (which is what the original code
        called, producing "'AudioDevice' object has no attribute
        'Activate'" on real Windows). Prefer the new property; fall back
        to the old Activate()+cast() path so this keeps working on older
        pycaw versions too."""
        from pycaw.pycaw import AudioUtilities

        devices = AudioUtilities.GetSpeakers()

        # New pycaw (confirmed on 1.4.16): a convenience property that
        # returns the ready-to-use IAudioEndpointVolume interface.
        endpoint = getattr(devices, "EndpointVolume", None)
        if endpoint is not None:
            return endpoint

        # Older pycaw: manual COM activation.
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume

        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def _set_mute(self, task: Task, mute: bool) -> AgentResult:
        try:
            volume = self._get_volume_interface()
            volume.SetMute(1 if mute else 0, None)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="Muted." if mute else "Unmuted.",
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't change mute state (volume control needs Windows): {e}",
                error="volume_not_supported",
            )

    def _set_volume(self, task: Task, intent) -> AgentResult:
        try:
            pct = max(0, min(100, intent.numeric_arg))
            volume = self._get_volume_interface()
            volume.SetMasterVolumeLevelScalar(pct / 100.0, None)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Volume set to {pct}%.",
                data={"volume": pct},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't set volume (volume control needs Windows): {e}",
                error="volume_not_supported",
            )

    def _adjust_volume(self, task: Task, delta: int) -> AgentResult:
        try:
            volume = self._get_volume_interface()
            current = volume.GetMasterVolumeLevelScalar()
            new_pct = max(0, min(100, round(current * 100) + delta))
            volume.SetMasterVolumeLevelScalar(new_pct / 100.0, None)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Volume set to {new_pct}%.",
                data={"volume": new_pct},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't adjust volume (volume control needs Windows): {e}",
                error="volume_not_supported",
            )

    # ---- Brightness (SENSITIVE) --------------------------------------------

    def _set_brightness(self, task: Task, intent) -> AgentResult:
        try:
            import screen_brightness_control as sbc
            pct = max(0, min(100, intent.numeric_arg))
            sbc.set_brightness(pct)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Brightness set to {pct}%.",
                data={"brightness": pct},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't set brightness (needs a display this machine controls): {e}",
                error="brightness_not_supported",
            )

    def _adjust_brightness(self, task: Task, delta: int) -> AgentResult:
        try:
            import screen_brightness_control as sbc
            current = sbc.get_brightness()[0]
            new_pct = max(0, min(100, current + delta))
            sbc.set_brightness(new_pct)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Brightness set to {new_pct}%.",
                data={"brightness": new_pct},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't adjust brightness (needs a display this machine controls): {e}",
                error="brightness_not_supported",
            )

    # ---- Launch/close application ------------------------------------------

    def _launch_app(self, task: Task, intent) -> AgentResult:
        name = intent.text_arg
        try:
            if sys.platform.startswith("win"):
                import os
                os.startfile(name)
            else:
                subprocess.Popen([name])
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Launched {name}.",
                data={"app": name},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't launch '{name}': {e}",
                error="launch_failed",
            )

    def _close_app(self, task: Task, intent) -> AgentResult:
        """DESTRUCTIVE -- by the time this runs, the orchestrator's
        central confirmation gate has already required explicit approval
        (see core/orchestrator.py). Matches by case-insensitive substring
        against EITHER the running process's name OR its full command
        line -- fully real, fully testable, no platform-specific
        dependency at all.

        Matching against cmdline too (not just the bare process name) is
        a real improvement, not just a testing convenience: a generic
        name like "python.exe" matches every Python process on the
        system, but a person (or a test) can give a more specific
        fragment of the actual command line to target one precisely.

        Explicitly excludes SARVOS's own process (os.getpid()) from ever
        being matched -- found necessary directly: a generic name like
        "python3" (which SARVOS itself runs as) could otherwise match and
        terminate SARVOS's own process instead of (or in addition to) the
        one the person actually meant, discovered when a test using this
        exact generic name accidentally terminated the test runner
        itself."""
        import os

        name = intent.text_arg.lower()
        own_pid = os.getpid()
        matched = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["pid"] == own_pid:
                    continue
                proc_name = (p.info["name"] or "").lower()
                cmdline = " ".join(p.info["cmdline"] or []).lower()
                if name in proc_name or name in cmdline:
                    p.terminate()
                    matched.append(p.info["name"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not matched:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"No running process matching '{intent.text_arg}' found.",
                error="process_not_found",
            )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Closed {len(matched)} process(es) matching '{intent.text_arg}': "
                   f"{', '.join(matched)}",
            data={"closed": matched},
        )

    # ---- Power state (DESTRUCTIVE) -----------------------------------------

    def _execute_system_command(self, args: list[str]) -> tuple[bool, str]:
        """Isolated on purpose: tests replace this method entirely so the
        confirmation-gating logic can be proven WITHOUT ever risking a
        real shutdown/restart during a test run.

        CRITICAL DEFENSE-IN-DEPTH, added after a real near-miss: a test
        that assumed it would only ever run on a non-Windows sandbox
        (and therefore never reach this method for real) was in fact run
        on a real Windows machine, where it fell through to a genuine,
        unmocked `shutdown /s /t 0` call. It only failed to actually shut
        the machine down because the screen happened to be locked at
        that exact moment (Windows refuses a remote/API shutdown while
        locked without a force flag) -- not because of any safeguard
        here. That was luck, not protection. This check closes that gap:
        pytest sets PYTEST_CURRENT_TEST for the duration of every test
        it runs, so refusing to proceed when it's set means a test that
        forgets to mock this method fails loudly and safely instead of
        silently attempting a real, irreversible system command."""
        import os

        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise RuntimeError(
                "_execute_system_command was reached for REAL during a "
                "pytest run without being mocked -- refusing to execute "
                "a real shutdown/restart/sleep command. If this test is "
                "intentionally exercising this path, mock "
                "_execute_system_command explicitly."
            )
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=10)
            return result.returncode == 0, (result.stdout or "") + (result.stderr or "")
        except Exception as e:
            return False, str(e)

    def _shutdown(self, task: Task, intent) -> AgentResult:
        if not sys.platform.startswith("win"):
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Shutdown isn't implemented for this platform ({platform.system()}).",
                error="shutdown_not_supported",
            )
        ok, output = self._execute_system_command(["shutdown", "/s", "/t", "0"])
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=ok,
            output="Shutting down." if ok else f"Couldn't shut down: {output}",
        )

    def _restart(self, task: Task, intent) -> AgentResult:
        if not sys.platform.startswith("win"):
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Restart isn't implemented for this platform ({platform.system()}).",
                error="restart_not_supported",
            )
        ok, output = self._execute_system_command(["shutdown", "/r", "/t", "0"])
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=ok,
            output="Restarting." if ok else f"Couldn't restart: {output}",
        )

    def _sleep(self, task: Task, intent) -> AgentResult:
        if not sys.platform.startswith("win"):
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Sleep isn't implemented for this platform ({platform.system()}).",
                error="sleep_not_supported",
            )
        ok, output = self._execute_system_command(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
        )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=ok,
            output="Going to sleep." if ok else f"Couldn't sleep: {output}",
        )
