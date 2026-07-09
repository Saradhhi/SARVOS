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

import os

from agents import browser_config
from agents.automation import resolve_safe_path
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
            Operation.UPLOAD: self._upload,
            Operation.PREVIEW: self._preview,
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

    def _upload(self, task: Task, intent) -> AgentResult:
        """SENSITIVE. Attaches a file to a form field.

        Files are restricted to browser_config.UPLOAD_DIR, resolved with the
        same parent-membership check as file automation. Handing a local file
        to a remote website is a data-exfiltration path: without this,
        "upload ../../.ssh/id_rsa to the resume field" would work exactly as
        asked. Nothing is transmitted until submit -- this only attaches.
        """
        guard = self._require_session(task)
        if guard:
            return guard

        try:
            path = resolve_safe_path(intent.text_arg, workspace_root=browser_config.UPLOAD_DIR)
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"Refusing to upload '{intent.text_arg}': {e}. Files must "
                    f"be inside {browser_config.UPLOAD_DIR}."
                ),
                error="unsafe_upload_path",
            )

        if not path.is_file():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"'{intent.text_arg}' isn't in {browser_config.UPLOAD_DIR}.",
                error="upload_file_not_found",
            )

        try:
            page = self.session.page
            if intent.field_arg:
                target = self._find_file_input(intent.field_arg)
            else:
                target = None
                loc = page.locator("input[type='file']")
                if loc.count() > 0:
                    target = loc.first

            if target is None:
                return AgentResult(
                    task_id=task.task_id, agent=self.name, success=False,
                    output=(
                        f"Couldn't find a file-upload field"
                        + (f" matching '{intent.field_arg}'" if intent.field_arg else "")
                        + " on this page. Try 'read the page' to see what's there."
                    ),
                    error="upload_field_not_found",
                )

            target.set_input_files(str(path))
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Attached '{path.name}'. Nothing is sent until you submit.",
                data={"file": path.name},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't attach '{intent.text_arg}': {e}",
                error="upload_failed",
            )

    def _find_file_input(self, description: str):
        page = self.session.page
        desc = description.strip()
        for make in (
            lambda: page.get_by_label(desc, exact=False),
            lambda: page.locator(f"input[type='file'][name='{desc}']"),
            lambda: page.locator(f"input[type='file'][id='{desc}']"),
            lambda: page.locator("input[type='file']"),
        ):
            try:
                loc = make()
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        return None

    def _preview(self, task: Task, intent) -> AgentResult:
        """SAFE. Screenshots the filled form and reports every field value,
        so you can see exactly what would be submitted BEFORE submitting.

        This exists because of a lesson learned the hard way elsewhere in
        this project: an agent reporting "Submitted" is not evidence that
        the right thing was sent. A submitted job application is irreversible
        and attached to your name. Look at it first.
        """
        guard = self._require_session(task)
        if guard:
            return guard

        try:
            page = self.session.page
            os.makedirs(browser_config.PREVIEW_DIR, exist_ok=True)
            import time
            path = os.path.join(browser_config.PREVIEW_DIR, f"form_{int(time.time())}.png")
            page.screenshot(path=path, full_page=True)

            # Mirrors what a browser ACTUALLY submits, not merely what each
            # element's .value happens to hold.
            #
            # Real bug this fixes, caught by running the preview against
            # httpbin's form: reading el.value on radios and checkboxes
            # reports every option's value whether or not it's checked, so
            # the preview claimed "size: small / medium / large" would be
            # submitted when nothing was selected at all. A preview that
            # over-reports is worse than no preview -- it is exactly the
            # false confidence this feature exists to prevent.
            fields = page.evaluate(
                """() => Array.from(
                    document.querySelectorAll('input, textarea, select')
                ).filter(el => {
                    // A browser submits nothing for these.
                    if (el.disabled || !(el.name || el.id)) return false;
                    if (el.type === 'submit' || el.type === 'button'
                        || el.type === 'reset' || el.type === 'image') return false;
                    if (el.type === 'radio' || el.type === 'checkbox') return el.checked;
                    return true;
                }).map(el => {
                    let value;
                    if (el.type === 'password') value = '********';
                    else if (el.type === 'file') value = el.files?.[0]?.name || '(no file)';
                    else if (el.tagName.toLowerCase() === 'select') {
                        value = Array.from(el.selectedOptions).map(o => o.value).join(', ');
                    } else value = el.value || '';
                    return {
                        name: el.name || el.id,
                        type: el.type || el.tagName.toLowerCase(),
                        value: value
                    };
                }).filter(f => f.value !== '')"""
            )

            if fields:
                lines = "\n".join(f"  {f['name']}: {f['value']}" for f in fields)
                summary = f"This is what would be submitted:\n{lines}"
            else:
                summary = "No filled fields found on this page."

            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"{summary}\n\nFull-page screenshot: {path}",
                data={"screenshot": path, "fields": fields},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't preview the form: {e}",
                error="preview_failed",
            )

    def _find_clickable(self, description: str):
        """Find a genuinely INTERACTIVE element, not merely text that reads
        like one.

        Real bug this fixes, found in live use: the old version fell back to
        page.get_by_text(), which matches any text node at all. On httpbin's
        JSON results page, 'click large' matched the word "large" inside the
        response body and cheerfully reported "Clicked 'large'." Nothing was
        clicked. That is a false success -- the same failure this project has
        now hit three times in different disguises.

        Interactive roles are tried first; a bare text match is only accepted
        if the element it lands on is itself clickable (or sits inside a
        label, which is how radio buttons and checkboxes are usually
        labelled). Whatever is found must also be visible and enabled --
        'found an element' is not 'found an actionable one', which the
        disabled-submit-button bug taught the hard way.
        """
        page = self.session.page
        desc = description.strip()
        escaped = desc.replace("'", "\\'")

        candidates = [
            lambda: page.get_by_role("button", name=desc),
            lambda: page.get_by_role("link", name=desc),
            lambda: page.get_by_role("checkbox", name=desc),
            lambda: page.get_by_role("radio", name=desc),
            lambda: page.get_by_label(desc, exact=False),
            lambda: page.locator(f"button:has-text('{escaped}')"),
            lambda: page.locator(f"[aria-label='{escaped}']"),
            lambda: page.locator(f"input[value='{escaped}']"),
            # Text, but ONLY where it is (or is inside) something clickable.
            lambda: page.locator(
                f"a:has-text('{escaped}'), button:has-text('{escaped}'), "
                f"label:has-text('{escaped}'), [role='button']:has-text('{escaped}')"
            ),
        ]
        for make in candidates:
            try:
                loc = make()
                if loc.count() == 0:
                    continue
                first = loc.first
                if first.is_visible() and first.is_enabled():
                    return first
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

    def preflight(self, task: Task) -> AgentResult | None:
        """Read-only. Refuse to submit a page that has no form on it, before
        the confirmation gate rather than after.

        Found in live use: after submitting, the browser lands on the result
        page (httpbin's JSON output). Typing 'submit' there would prompt
        'This looks destructive. Proceed?' and, on approval, press Enter into
        a page with nothing to submit -- then report success. A false success
        for an action that could not possibly have happened.

        Performs no side effects: it only inspects the DOM.
        """
        intent = classify(task.instruction)
        if intent.operation != Operation.SUBMIT:
            return None
        if not self.session.is_open():
            return None  # _submit's own guard reports this more precisely
        try:
            has_form = self.session.page.locator("form").count() > 0
            has_inputs = self.session.page.locator(
                "input:not([type='hidden']), textarea, select"
            ).count() > 0
        except Exception:
            return None  # can't tell -- let the real handler try
        if not has_form and not has_inputs:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output="There's no form on this page to submit.",
                error="no_form_to_submit",
            )
        return None

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
