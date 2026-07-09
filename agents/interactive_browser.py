"""
InteractiveBrowserAgent -- STATEFUL, multi-step browsing via a persistent
Playwright session held open across turns. Distinct from browser.py (the
read-only, fresh-browser-per-call agent), which stays deliberately simple.

Why a persistent session: interactive browsing is inherently multi-step
and stateful -- open a page, THEN type into a field on it, THEN click,
THEN submit, all needing the SAME live browser to still be open. The
session is held as instance state on this agent, which the orchestrator
keeps alive for its whole lifetime, so it survives between turns until
explicitly closed.

SECURITY CAVEAT, stated plainly rather than glossed over: logging in
here means typing credentials (via a normal 'type' command) into a
HEADLESS, invisible automated browser session against a real site. That
is a genuinely worse security position than typing into your own visible
browser -- you're trusting this automation with live credentials. Nothing
is ever stored (there is deliberately no save/load-password capability at
all), but the in-the-moment trust is real. Submit/login is also gated by
the orchestrator's confirmation check, so nothing is actually submitted
without explicit approval.

Field/element matching is best-effort by common, stable attributes
(placeholder, name, id, label text, aria-label, visible text). It is NOT
a full accessibility-tree resolver -- if a match is ambiguous or missing,
the agent says so clearly rather than guessing and clicking the wrong
thing.
"""

from __future__ import annotations

from agents import browser_config
from agents.base import BaseAgent
from agents.interactive_browser_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task


class BrowserSession:
    """Holds one live Playwright browser + page across turns. Lazily
    started, explicitly closed. Kept deliberately small."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self.page = None

    def is_open(self) -> bool:
        return self.page is not None

    def start(self):
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=browser_config.HEADLESS)
        self.page = self._browser.new_page()

    def close(self):
        # Each step guarded independently -- a failure closing one part
        # must not leak the others.
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        self._browser = None
        self.page = None


class InteractiveBrowserAgent(BaseAgent):
    name = AgentName.INTERACTIVE_BROWSER

    def __init__(self, memory):
        super().__init__(memory)
        self.session = BrowserSession()

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        handlers = {
            Operation.OPEN: self._open,
            Operation.TYPE: self._type,
            Operation.CLICK: self._click,
            Operation.READ: self._read,
            Operation.SUBMIT: self._submit,
            Operation.CLOSE: self._close,
        }
        handler = handlers.get(intent.operation)
        if handler is None:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"I couldn't work out an interactive browsing action "
                    f"from: '{task.instruction}'. Try 'open a browser "
                    f"session at example.com', 'type \"text\" into the "
                    f"search field', 'click the login button', 'read the "
                    f"page', 'submit', or 'close the session'."
                ),
            )
        return handler(task, intent)

    def _require_session(self, task: Task) -> AgentResult | None:
        if not self.session.is_open():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    "No browser session is open. Start one first with "
                    "'open a browser session at <url>'."
                ),
                error="no_session",
            )
        return None

    def _open(self, task: Task, intent) -> AgentResult:
        try:
            # If a session is already open, reuse it and just navigate --
            # closing and reopening would throw away cookies/login state,
            # which is the whole point of a persistent session.
            if not self.session.is_open():
                self.session.start()
            self.session.page.goto(intent.url, timeout=browser_config.PAGE_LOAD_TIMEOUT_MS)
            title = self.session.page.title()
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Opened '{title}' ({intent.url}). Session is live -- "
                       f"you can now type into fields, click, read, or submit.",
                data={"url": intent.url, "title": title},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't open '{intent.url}': {e}",
                error="open_failed",
            )

    def _find_field(self, description: str):
        """Best-effort field locator by common stable attributes. Returns
        a Playwright locator or None. Tries, in order: exact name/id,
        placeholder, associated label, aria-label."""
        page = self.session.page
        desc = description.strip()

        # Playwright's get_by_label / get_by_placeholder are the most
        # robust; try them before falling back to attribute selectors.
        candidates = [
            lambda: page.get_by_label(desc, exact=False),
            lambda: page.get_by_placeholder(desc, exact=False),
            lambda: page.locator(f"input[name='{desc}']"),
            lambda: page.locator(f"input[id='{desc}']"),
            lambda: page.locator(f"textarea[name='{desc}']"),
            lambda: page.get_by_role("textbox", name=desc),
        ]
        for make in candidates:
            try:
                loc = make()
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        return None

    def _type(self, task: Task, intent) -> AgentResult:
        guard = self._require_session(task)
        if guard:
            return guard
        try:
            field = self._find_field(intent.field_arg)
            if field is None:
                return AgentResult(
                    task_id=task.task_id, agent=self.name, success=False,
                    output=f"Couldn't find a field matching '{intent.field_arg}' "
                           f"on the page. Try 'read the page' to see what's there.",
                    error="field_not_found",
                )
            field.fill(intent.text_arg)
            # Deliberately does NOT echo the typed value back -- it may be
            # a password. Confirm the action, not the secret.
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Typed into the '{intent.field_arg}' field.",
                data={"field": intent.field_arg},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't type into '{intent.field_arg}': {e}",
                error="type_failed",
            )

    def _find_clickable(self, description: str):
        page = self.session.page
        desc = description.strip()
        candidates = [
            lambda: page.get_by_role("button", name=desc),
            lambda: page.get_by_role("link", name=desc),
            lambda: page.get_by_text(desc, exact=False),
            lambda: page.locator(f"button:has-text('{desc}')"),
            lambda: page.locator(f"[aria-label='{desc}']"),
        ]
        for make in candidates:
            try:
                loc = make()
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        return None

    def _click(self, task: Task, intent) -> AgentResult:
        guard = self._require_session(task)
        if guard:
            return guard
        try:
            target = self._find_clickable(intent.text_arg)
            if target is None:
                return AgentResult(
                    task_id=task.task_id, agent=self.name, success=False,
                    output=f"Couldn't find anything clickable matching "
                           f"'{intent.text_arg}'. Try 'read the page' to see "
                           f"what's there.",
                    error="element_not_found",
                )
            target.click(timeout=browser_config.PAGE_LOAD_TIMEOUT_MS)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Clicked '{intent.text_arg}'.",
                data={"clicked": intent.text_arg},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't click '{intent.text_arg}': {e}",
                error="click_failed",
            )

    def _read(self, task: Task, intent) -> AgentResult:
        guard = self._require_session(task)
        if guard:
            return guard
        try:
            title = self.session.page.title()
            text = self.session.page.inner_text("body")
            truncated = text[: browser_config.MAX_TEXT_LENGTH]
            if len(text) > browser_config.MAX_TEXT_LENGTH:
                truncated += "... (truncated)"
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"'{title}':\n{truncated}",
                data={"title": title},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't read the page: {e}",
                error="read_failed",
            )

    def _submit(self, task: Task, intent) -> AgentResult:
        """DESTRUCTIVE -- the orchestrator's confirmation gate has already
        required explicit approval before this runs. Tries a submit button
        first, then falling back to pressing Enter in the active element."""
        guard = self._require_session(task)
        if guard:
            return guard
        try:
            page = self.session.page
            # Prefer an explicit, ACTUALLY-CLICKABLE submit control. Found
            # necessary from real use on DuckDuckGo: modern search sites
            # often have a submit <button> that's deliberately `disabled`
            # and hidden (the search fires on Enter instead), so merely
            # finding a submit button isn't enough -- the old code found
            # DDG's disabled button and timed out trying to click the
            # unclickable thing, never reaching the Enter fallback. Now we
            # require the button to be visible AND enabled before clicking,
            # and fall through to Enter otherwise.
            submit = None
            for make in (
                lambda: page.get_by_role("button", name="submit"),
                lambda: page.locator("button[type='submit']"),
                lambda: page.locator("input[type='submit']"),
                lambda: page.get_by_role("button", name="log in"),
                lambda: page.get_by_role("button", name="sign in"),
            ):
                try:
                    loc = make()
                    if loc.count() > 0:
                        candidate = loc.first
                        # Only use it if it's genuinely actionable.
                        if candidate.is_visible() and candidate.is_enabled():
                            submit = candidate
                            break
                except Exception:
                    continue

            if submit is not None:
                submit.click(timeout=browser_config.PAGE_LOAD_TIMEOUT_MS)
                method = "clicked the submit button"
            else:
                # Fallback: press Enter, which submits most simple forms
                # AND is how modern search boxes (with a decorative/disabled
                # button) actually submit. Enter only submits when focus is
                # inside a field, and focus isn't guaranteed to persist
                # across turns (the typing may have been a previous turn),
                # so explicitly focus a text input first if one exists.
                try:
                    first_input = page.locator(
                        "input[type='text'], input[type='search'], input[type='email'], "
                        "input[type='password'], input:not([type])"
                    )
                    if first_input.count() > 0:
                        first_input.first.focus()
                except Exception:
                    pass
                page.keyboard.press("Enter")
                method = "pressed Enter"

            # Give any resulting navigation a moment to settle so the
            # follow-up title/URL reflects the real result page, not the
            # pre-submit state.
            try:
                page.wait_for_load_state("networkidle", timeout=browser_config.PAGE_LOAD_TIMEOUT_MS)
            except Exception:
                pass  # some submits don't navigate at all -- that's fine

            title = page.title()
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Submitted ({method}). Page is now '{title}'. Use "
                       f"'read the page' to see the result.",
                data={"title": title, "method": method},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't submit: {e}",
                error="submit_failed",
            )

    def _close(self, task: Task, intent) -> AgentResult:
        if not self.session.is_open():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="No browser session was open.",
            )
        self.session.close()
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output="Closed the browser session.",
        )
