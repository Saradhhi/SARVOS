"""
WindowManagerAgent -- list, focus, minimize/maximize/restore, move, resize,
and close desktop windows.

HONEST TESTING LIMITATION, stated plainly: this was written in a headless
Linux container where `pygetwindow` does not merely fail to work -- it
raises NotImplementedError on IMPORT. Not one real window operation could
be executed here. That is worse than any other agent in this project
(pycaw at least imported; the screenshot failure path was exercisable).

Given how many real bugs this project's Windows testing has caught that the
sandbox structurally could not -- the pycaw API change, the battery data
shape, the shutdown near-miss -- writing several hundred untested lines of
window code would have been reckless. So every Windows call is isolated
behind WindowBackend, a thin seam with no logic in it. Everything that CAN
be tested (routing, risk tiers, title matching, ambiguity handling, error
paths, the confirmation gate) is tested against a fake backend. Only the
seam itself awaits verification on real hardware.

RISK TIERS:
- SAFE:        list, active
- SENSITIVE:   focus, minimize, maximize, restore, move, resize
- DESTRUCTIVE: close (can lose unsaved work -- gated by the orchestrator)
"""

from __future__ import annotations

from agents.base import BaseAgent
from agents.window_manager_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task

MAX_WINDOWS_LISTED = 20


class WindowUnavailable(Exception):
    """The window backend can't run here at all (wrong OS, missing lib)."""


class WindowBackend:
    """The ONLY place that touches the real windowing API.

    Deliberately contains no logic -- it is a seam, so that everything with
    behavior worth testing can be tested against a substitute. `pygetwindow`
    is imported lazily inside each method: on Linux it raises
    NotImplementedError at import time, which would otherwise take down this
    entire module (and the whole agent registry with it).
    """

    def _gw(self):
        try:
            import pygetwindow as gw
        except Exception as e:  # NotImplementedError on Linux, ImportError if absent
            raise WindowUnavailable(
                f"Window management needs Windows (pygetwindow): {e}"
            ) from e
        return gw

    def all_windows(self) -> list:
        gw = self._gw()
        return [w for w in gw.getAllWindows() if (w.title or "").strip()]

    def active_window(self):
        return self._gw().getActiveWindow()

    # Each of these takes a backend window object, so the agent never
    # touches the library's types directly.
    def focus(self, win) -> None:
        win.activate()

    def minimize(self, win) -> None:
        win.minimize()

    def maximize(self, win) -> None:
        win.maximize()

    def restore(self, win) -> None:
        win.restore()

    def move(self, win, x: int, y: int) -> None:
        win.moveTo(x, y)

    def resize(self, win, width: int, height: int) -> None:
        win.resizeTo(width, height)

    def close(self, win) -> None:
        win.close()

    @staticmethod
    def describe(win) -> str:
        return (win.title or "").strip()


class WindowManagerAgent(BaseAgent):
    name = AgentName.WINDOW_MANAGER

    def __init__(self, memory, backend: WindowBackend | None = None):
        super().__init__(memory)
        self.backend = backend or WindowBackend()

    def preflight(self, task: Task) -> AgentResult | None:
        """Read-only. Resolve the target window before the confirmation gate,
        so 'close the notepad window' with no Notepad open fails immediately
        instead of asking the person to authorize closing nothing.

        Performs no side effects -- it only reads the window list."""
        intent = classify(task.instruction)
        if intent.operation in (Operation.UNKNOWN, Operation.LIST, Operation.ACTIVE):
            return None
        try:
            _win, err = self._resolve_target(intent)
        except WindowUnavailable as e:
            return self._fail(task, str(e), error="window_unavailable")
        if err:
            return self._fail(task, err, error="window_not_resolved")
        return None

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        if intent.operation == Operation.UNKNOWN:
            return self._fail(
                task,
                "I couldn't work out a window action from: "
                f"'{task.instruction}'. Try 'list windows', 'minimize', "
                f"'focus notepad', 'move notepad to 100, 200', "
                f"'resize notepad to 800x600', or 'close the notepad window'.",
            )

        try:
            if intent.operation == Operation.LIST:
                return self._list(task)
            if intent.operation == Operation.ACTIVE:
                return self._active(task)
            return self._act_on_window(task, intent)
        except WindowUnavailable as e:
            return self._fail(task, str(e), error="window_unavailable")

    # ---- helpers ---------------------------------------------------------

    def _fail(self, task: Task, message: str, error: str = "window_bad_request") -> AgentResult:
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=False,
            output=message, error=error,
        )

    def _resolve_target(self, intent):
        """Return (window, error_message). A None target means the active
        window. Ambiguous title matches are refused rather than guessed --
        silently minimizing the wrong window is a bad surprise, and silently
        CLOSING the wrong one could lose real work."""
        if intent.target is None:
            win = self.backend.active_window()
            if win is None:
                return None, "There's no active window right now."
            return win, None

        needle = intent.target.lower()
        matches = [
            w for w in self.backend.all_windows()
            if needle in self.backend.describe(w).lower()
        ]
        if not matches:
            return None, f"No open window matching '{intent.target}'."
        if len(matches) > 1:
            titles = ", ".join(f"'{self.backend.describe(w)}'" for w in matches[:5])
            return None, (
                f"'{intent.target}' matches {len(matches)} windows ({titles}). "
                f"Be more specific -- I won't guess which one you meant."
            )
        return matches[0], None

    # ---- SAFE ------------------------------------------------------------

    def _list(self, task: Task) -> AgentResult:
        windows = self.backend.all_windows()
        if not windows:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="No open windows with titles.", data={"windows": []},
            )
        titles = [self.backend.describe(w) for w in windows[:MAX_WINDOWS_LISTED]]
        more = len(windows) - len(titles)
        listing = "\n".join(f"  {t}" for t in titles)
        suffix = f"\n  ... and {more} more" if more > 0 else ""
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"{len(windows)} open window(s):\n{listing}{suffix}",
            data={"windows": titles, "total": len(windows)},
        )

    def _active(self, task: Task) -> AgentResult:
        win = self.backend.active_window()
        if win is None:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="There's no active window right now.", data={"active": None},
            )
        title = self.backend.describe(win)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Active window: {title}", data={"active": title},
        )

    # ---- SENSITIVE + DESTRUCTIVE ----------------------------------------

    def _act_on_window(self, task: Task, intent) -> AgentResult:
        win, err = self._resolve_target(intent)
        if err:
            return self._fail(task, err, error="window_not_resolved")

        title = self.backend.describe(win)
        op = intent.operation
        try:
            if op == Operation.FOCUS:
                self.backend.focus(win)
                msg = f"Focused '{title}'."
            elif op == Operation.MINIMIZE:
                self.backend.minimize(win)
                msg = f"Minimized '{title}'."
            elif op == Operation.MAXIMIZE:
                self.backend.maximize(win)
                msg = f"Maximized '{title}'."
            elif op == Operation.RESTORE:
                self.backend.restore(win)
                msg = f"Restored '{title}'."
            elif op == Operation.MOVE:
                self.backend.move(win, intent.x, intent.y)
                msg = f"Moved '{title}' to ({intent.x}, {intent.y})."
            elif op == Operation.RESIZE:
                self.backend.resize(win, intent.width, intent.height)
                msg = f"Resized '{title}' to {intent.width}x{intent.height}."
            elif op == Operation.CLOSE:
                # DESTRUCTIVE -- by the time this runs, the orchestrator has
                # already required an explicit confirmation.
                self.backend.close(win)
                msg = f"Closed '{title}'."
            else:  # pragma: no cover -- guarded by classify()
                return self._fail(task, f"Unsupported window operation: {op}")
        except WindowUnavailable:
            raise
        except Exception as e:
            return self._fail(
                task, f"Couldn't {op.value} '{title}': {e}", error=f"window_{op.value}_failed"
            )

        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=msg, data={"window": title, "operation": op.value},
        )
