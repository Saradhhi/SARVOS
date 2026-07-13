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

import hashlib
import os
import time
from pathlib import Path

from agents import browser_config
from agents.automation import resolve_safe_path
from agents.base import BaseAgent
from agents.interactive_browser_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task


# Resolves each field's human label the way a browser does, so messy forms
# (name="input-42", label="Work Authorization") are still understandable.
_INSPECT_JS = r"""
() => {
  const labelFor = (el) => {
    // 1. <label for=id>
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && l.innerText.trim()) return l.innerText.trim();
    }
    // 2. aria-label
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    // 3. aria-labelledby
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const parts = lb.split(/\s+/).map(id => {
        const n = document.getElementById(id); return n ? n.innerText.trim() : '';
      }).filter(Boolean);
      if (parts.length) return parts.join(' ');
    }
    // 4. wrapping <label>
    const wrap = el.closest('label');
    if (wrap && wrap.innerText.trim()) return wrap.innerText.trim();
    // 5. placeholder
    if (el.placeholder) return el.placeholder.trim();
    return '';
  };
  const isRequired = (el) =>
    el.required || el.getAttribute('aria-required') === 'true';

  const out = [];
  const els = document.querySelectorAll('input, textarea, select');
  for (const el of els) {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden','submit','button','reset','image'].includes(type)) continue;
    if (el.disabled) continue;
    let options = [];
    if (el.tagName.toLowerCase() === 'select') {
      // Drop the empty-value placeholder option ('Select', 'Please choose'):
      // its value is '', so it's a prompt, not a real choice.
      options = Array.from(el.options)
        .filter(o => (o.value || '').trim() !== '')
        .map(o => o.value || o.text)
        .filter(Boolean);
    }
    out.push({
      name: el.name || el.id || '',
      label: labelFor(el),
      type: type,
      required: isRequired(el),
      options: options,
    });
  }
  // De-dupe radio groups: one entry per name, options = the values.
  const byName = {};
  const result = [];
  for (const f of out) {
    if (f.type === 'radio' && f.name) {
      if (!byName[f.name]) { byName[f.name] = {...f, options: []}; result.push(byName[f.name]); }
      // collect the radio value under options
      continue;
    }
    result.push(f);
  }
  // second pass to gather radio option values
  for (const el of els) {
    if ((el.type||'').toLowerCase() === 'radio' && el.name && byName[el.name]) {
      if (el.value) byName[el.name].options.push(el.value);
    }
  }
  return result;
}
"""


class BrowserSession:
    """Holds one live Playwright browser + one or more tabs across turns.

    `page` stays a property pointing at the ACTIVE tab, so every existing
    operation (type, click, submit, preview...) keeps working unchanged and
    simply acts on whichever tab is in focus. Tabs are Playwright pages
    inside a single browser context, which is also what makes downloads
    work: `accept_downloads` is a context-level setting.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self.pages: list = []
        self.active_index: int = 0

    @property
    def page(self):
        """The active tab, or None if no session is open."""
        if not self.pages:
            return None
        return self.pages[self.active_index]

    def is_open(self) -> bool:
        return bool(self.pages)

    def start(self):
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=browser_config.HEADLESS)
        # accept_downloads must be set on the CONTEXT, not the page.
        self._context = self._browser.new_context(accept_downloads=True)
        self.pages = [self._context.new_page()]
        self.active_index = 0

    def new_tab(self, url: str | None = None):
        page = self._context.new_page()
        self.pages.append(page)
        self.active_index = len(self.pages) - 1
        if url:
            page.goto(url, timeout=browser_config.PAGE_LOAD_TIMEOUT_MS)
        return page

    def switch_to(self, index: int) -> bool:
        if not 0 <= index < len(self.pages):
            return False
        self.active_index = index
        try:
            self.pages[index].bring_to_front()
        except Exception:
            pass  # headless: bring_to_front is a no-op that may not exist
        return True

    def close_tab(self, index: int) -> bool:
        """Closes one tab. Closing the last tab ends the session, which is
        what a browser does -- reported honestly rather than leaving an
        invisible zombie session behind."""
        if not 0 <= index < len(self.pages):
            return False
        try:
            self.pages[index].close()
        except Exception:
            pass
        self.pages.pop(index)
        if not self.pages:
            self.close()
            return True
        self.active_index = min(self.active_index, len(self.pages) - 1)
        return True

    def tab_titles(self) -> list[tuple[int, str, str]]:
        out = []
        for i, p in enumerate(self.pages):
            try:
                out.append((i, p.title() or "(untitled)", p.url))
            except Exception:
                out.append((i, "(unavailable)", ""))
        return out

    def close(self):
        # Each step guarded independently -- a failure closing one part
        # must not leak the others.
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        self._browser = None
        self._context = None
        self.pages = []
        self.active_index = 0


class InteractiveBrowserAgent(BaseAgent):
    name = AgentName.INTERACTIVE_BROWSER

    def __init__(self, memory):
        super().__init__(memory)
        self.session = BrowserSession()

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        handlers = {
            Operation.OPEN: self._open,
            Operation.NEW_TAB: self._new_tab,
            Operation.LIST_TABS: self._list_tabs,
            Operation.SWITCH_TAB: self._switch_tab,
            Operation.CLOSE_TAB: self._close_tab,
            Operation.DOWNLOAD: self._download,
            Operation.SAVE_PDF: self._save_pdf,
            Operation.BOOKMARK: self._bookmark,
            Operation.LIST_BOOKMARKS: self._list_bookmarks,
            Operation.OPEN_BOOKMARK: self._open_bookmark,
            Operation.CHECK_CHANGES: self._check_changes,
            Operation.TYPE: self._type,
            Operation.UPLOAD: self._upload,
            Operation.PREVIEW: self._preview,
            Operation.INSPECT: self._inspect,
            Operation.AUTOFILL: self._autofill,
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

    def _fail(self, task: Task, message: str, error: str) -> AgentResult:
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=False,
            output=message, error=error,
        )

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

    def _settle(self, page) -> None:
        """Wait for JS-rendered content to appear after a navigation.

        Plain goto() returns at the 'load' event, which fires before a
        single-page app has parsed and run its scripts -- that's why 'read
        the page' came back empty on a JS-heavy job aggregator.
        'domcontentloaded' waits for the parsed DOM, which is the wait that
        actually fixes that, and it returns quickly.

        We deliberately do NOT wait for 'networkidle' by default: testing
        showed it holds the page open long enough (many sites never go idle
        -- analytics, sockets, long-poll) to exhaust browser resources under
        load, and its marginal benefit over domcontentloaded is small.
        SARVOS_WAIT_NETWORKIDLE=1 opts in for the rare site that needs it.

        Tolerates its timeout rather than raising: a settled-enough page
        beats an error.
        """
        try:
            page.wait_for_load_state("domcontentloaded",
                                     timeout=browser_config.SETTLE_TIMEOUT_MS)
        except Exception:
            pass
        if browser_config.WAIT_NETWORKIDLE:
            try:
                page.wait_for_load_state("networkidle",
                                         timeout=browser_config.NETWORKIDLE_BUDGET_MS)
            except Exception:
                pass

    def _open(self, task: Task, intent) -> AgentResult:
        try:
            # If a session is already open, reuse it and just navigate --
            # closing and reopening would throw away cookies/login state,
            # which is the whole point of a persistent session.
            if not self.session.is_open():
                self.session.start()
            self.session.page.goto(intent.url, timeout=browser_config.PAGE_LOAD_TIMEOUT_MS)
            self._settle(self.session.page)
            title = self.session.page.title()
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Opened '{title or intent.url}' ({intent.url}). Session is live -- "
                       f"you can now type into fields, click, read, or submit.",
                data={"url": intent.url, "title": title},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't open '{intent.url}': {e}",
                error="open_failed",
            )

    def _inspect(self, task: Task, intent) -> AgentResult:
        """SAFE. Enumerate every fillable field on the page with its real
        name, human label, type, required flag, and (for selects) options.

        This is the thing that makes real job forms workable. On a Workday or
        Greenhouse form the `name` is often 'input-42' while the visible
        label is 'Work Authorization', so we resolve the label the way a
        browser does -- label[for], aria-label, aria-labelledby, a wrapping
        <label>, then placeholder -- rather than trusting the name alone."""
        guard = self._require_session(task)
        if guard:
            return guard
        try:
            fields = self.session.page.evaluate(_INSPECT_JS)
        except Exception as e:
            return self._fail(task, f"Couldn't inspect the form: {e}", "inspect_failed")

        if not fields:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="No fillable fields found on this page.",
                data={"fields": []},
            )

        lines = []
        for f in fields:
            req = " (required)" if f["required"] else ""
            opts = ""
            if f["options"]:
                shown = ", ".join(f["options"][:8])
                more = "..." if len(f["options"]) > 8 else ""
                opts = f"  options: [{shown}{more}]"
            label = f["label"] or "(no label)"
            lines.append(f"  {label} [{f['type']}]{req} -- name='{f['name']}'{opts}")

        n_req = sum(1 for f in fields if f["required"])
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=(
                f"{len(fields)} field(s), {n_req} required:\n" + "\n".join(lines) +
                "\n\nFill with 'type \"<value>\" into the <name or label> field', "
                "or 'autofill from <candidate>' to map your profile onto these."
            ),
            data={"fields": fields, "required_count": n_req},
        )

    # Maps a profile key to the words we'll look for in a form field's label
    # or name. Ordered by specificity so 'email' doesn't match 'email me
    # updates' style noise first. Kept explicit rather than fuzzy: a wrong
    # autofill (email typed into the referral box) is worse than a skip.
    _PROFILE_TO_FIELD_HINTS = {
        "email": ("email", "e-mail"),
        "phone": ("phone", "mobile", "telephone", "tel"),
        "full_name": ("full name", "your name", "name"),
        "first_name": ("first name", "given name", "forename"),
        "last_name": ("last name", "surname", "family name"),
        "linkedin": ("linkedin",),
        "github": ("github",),
        "portfolio": ("portfolio",),
        "website": ("website", "personal site"),
        "location": ("location", "city", "address"),
        "current_title": ("current title", "job title", "current role", "title"),
        "years_experience": ("years of experience", "years experience", "experience"),
        "work_authorization": ("work authorization", "authorization", "authorised",
                               "authorized", "visa", "sponsorship"),
        "salary_expectation": ("salary", "compensation", "expected pay"),
        "notice_period": ("notice period", "notice", "availability"),
    }

    def _autofill(self, task: Task, intent) -> AgentResult:
        """SENSITIVE. Inspects the form, maps the candidate's profile onto it,
        types only what it's confident about, and flags the rest -- required
        fields loudly. It NEVER submits. This is the honest version of "fill
        my application": it does the mechanical part and makes its own
        uncertainty visible, because a wrong autofill (your email in the
        'referral' box, or a silently skipped required field) is worse than
        no autofill at all. Review, then 'preview the form', then 'submit'.
        """
        guard = self._require_session(task)
        if guard:
            return guard

        # Resolve the candidate: named, or the job agent's active one.
        from agents.job import JobAgent
        job = JobAgent(self.memory)
        candidate = intent.name_arg or job._active_candidate()
        if not candidate:
            return self._fail(
                task,
                "No candidate to fill from. Name one ('autofill from alice') or "
                "set an active candidate first ('use candidate alice').",
                "no_candidate",
            )
        if not job._candidate_dir(candidate).is_dir():
            return self._fail(
                task, f"No candidate called '{candidate}'.", "candidate_not_found")
        profile = job._load_profile(candidate)
        if not profile:
            return self._fail(
                task,
                f"{candidate}'s profile is empty -- nothing to fill. Set fields "
                f"with 'set my email to ...' after 'use candidate {candidate}'.",
                "empty_profile",
            )

        # Inspect the live form.
        try:
            fields = self.session.page.evaluate(_INSPECT_JS)
        except Exception as e:
            return self._fail(task, f"Couldn't read the form: {e}", "inspect_failed")
        if not fields:
            return self._fail(task, "No fillable fields on this page.", "no_fields")

        filled, skipped_required, unmapped = [], [], []
        used_profile_keys = set()

        for f in fields:
            hay = f"{f['label']} {f['name']}".lower()
            ftype = f["type"]
            # Only fill free-text-ish inputs. Selects, radios, checkboxes,
            # and file inputs need a choice we won't guess -- flag them.
            fillable_type = ftype in ("text", "email", "tel", "url", "textarea", "search")

            match_key = None
            for pkey, hints in self._PROFILE_TO_FIELD_HINTS.items():
                if pkey in profile and pkey not in used_profile_keys:
                    if any(h in hay for h in hints):
                        match_key = pkey
                        break

            if match_key and fillable_type:
                loc = self._find_field(f["name"] or f["label"])
                if loc is not None:
                    try:
                        loc.fill(str(profile[match_key]))
                        filled.append((f["label"] or f["name"], match_key))
                        used_profile_keys.add(match_key)
                        continue
                    except Exception:
                        pass  # fall through to "couldn't fill" below

            # Not filled. Record why, so nothing silently vanishes.
            if f["required"]:
                reason = ("needs a choice" if not fillable_type
                          else "no matching profile field")
                skipped_required.append((f["label"] or f["name"], ftype, reason))
            elif match_key is None:
                unmapped.append((f["label"] or f["name"], ftype))

        # Build an honest report.
        lines = []
        if filled:
            lines.append(f"Filled {len(filled)} field(s) from {candidate}'s profile:")
            lines += [f"  {label}  <-  {key}" for label, key in filled]
        else:
            lines.append(f"Filled nothing -- no form field matched {candidate}'s "
                         f"profile confidently.")

        if skipped_required:
            lines.append("")
            lines.append(f"!! {len(skipped_required)} REQUIRED field(s) NOT filled "
                         f"-- you must handle these before submitting:")
            lines += [f"  {label} [{t}] -- {why}" for label, t, why in skipped_required]

        if unmapped:
            lines.append("")
            lines.append(f"{len(unmapped)} other field(s) left for you:")
            lines += [f"  {label} [{t}]" for label, t in unmapped]

        lines.append("")
        lines.append("Nothing was submitted. Review, then 'preview the form', "
                     "then 'submit' (you'll be asked to confirm).")

        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output="\n".join(lines),
            data={
                "candidate": candidate,
                "filled": [{"label": l, "profile_key": k} for l, k in filled],
                "skipped_required": [
                    {"label": l, "type": t, "reason": r} for l, t, r in skipped_required
                ],
                "unmapped": [{"label": l, "type": t} for l, t in unmapped],
            },
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


    # ---- Tabs -----------------------------------------------------------

    def _new_tab(self, task: Task, intent) -> AgentResult:
        guard = self._require_session(task)
        if guard:
            return guard
        try:
            page = self.session.new_tab(intent.url)
            if intent.url:
                self._settle(page)
            title = page.title() if intent.url else "(blank)"
            n = len(self.session.pages)
            where = f" at '{title}'" if intent.url else ""
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Opened tab {n}{where}. It's now the active tab.",
                data={"tab": n, "title": title},
            )
        except Exception as e:
            return self._fail(task, f"Couldn't open a new tab: {e}", "new_tab_failed")

    def _list_tabs(self, task: Task, intent) -> AgentResult:
        guard = self._require_session(task)
        if guard:
            return guard
        tabs = self.session.tab_titles()
        lines = []
        for i, title, url in tabs:
            marker = " *" if i == self.session.active_index else "  "
            lines.append(f"{marker} {i + 1}. {title} -- {url}")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"{len(tabs)} tab(s) (* = active):\n" + "\n".join(lines),
            data={"tabs": [{"index": i + 1, "title": t, "url": u} for i, t, u in tabs],
                  "active": self.session.active_index + 1},
        )

    def _switch_tab(self, task: Task, intent) -> AgentResult:
        guard = self._require_session(task)
        if guard:
            return guard
        if not self.session.switch_to(intent.tab_index):
            return self._fail(
                task,
                f"There's no tab {intent.tab_index + 1}. There are "
                f"{len(self.session.pages)}. Try 'list tabs'.",
                "no_such_tab",
            )
        title = self.session.page.title()
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Switched to tab {intent.tab_index + 1}: '{title}'.",
            data={"tab": intent.tab_index + 1, "title": title},
        )

    def _close_tab(self, task: Task, intent) -> AgentResult:
        """SENSITIVE, not DESTRUCTIVE. These are SARVOS's own headless tabs,
        containing only what SARVOS put there -- at worst an unsubmitted form
        you can refill. Contrast the window agent's `close`, which is
        DESTRUCTIVE because it can discard a person's unsaved work in a real
        application they were using."""
        guard = self._require_session(task)
        if guard:
            return guard
        n = len(self.session.pages)
        if not 0 <= intent.tab_index < n:
            return self._fail(
                task, f"There's no tab {intent.tab_index + 1}. There are {n}.", "no_such_tab"
            )
        was_last = n == 1
        self.session.close_tab(intent.tab_index)
        if was_last:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="Closed the last tab, which ended the browser session.",
                data={"session_closed": True},
            )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Closed tab {intent.tab_index + 1}. {len(self.session.pages)} left.",
            data={"tabs_remaining": len(self.session.pages)},
        )

    # ---- Downloads and PDF ----------------------------------------------

    @staticmethod
    def _safe_download_name(suggested: str) -> str:
        """A remote server chooses this name. Strip every path component --
        a suggested filename of '../../.ssh/authorized_keys' must land as
        'authorized_keys' inside the download directory, or not at all."""
        name = Path(suggested.replace("\\", "/")).name
        name = name.strip().lstrip(".") or "download"
        return "".join(c for c in name if c.isalnum() or c in "._- ")[:120] or "download"

    def _download(self, task: Task, intent) -> AgentResult:
        """SENSITIVE. Clicks a link and saves whatever it serves, into the
        sandboxed download directory only."""
        guard = self._require_session(task)
        if guard:
            return guard

        target = self._find_clickable(intent.text_arg)
        if target is None:
            return self._fail(
                task,
                f"Couldn't find anything clickable matching '{intent.text_arg}'. "
                f"Try 'read the page' to see what's there.",
                "element_not_found",
            )

        os.makedirs(browser_config.DOWNLOAD_DIR, exist_ok=True)
        try:
            page = self.session.page
            with page.expect_download(timeout=browser_config.PAGE_LOAD_TIMEOUT_MS) as info:
                target.click()
            download = info.value

            name = self._safe_download_name(download.suggested_filename or "download")
            dest = resolve_safe_path(name, workspace_root=browser_config.DOWNLOAD_DIR)
            download.save_as(str(dest))

            size = dest.stat().st_size
            if size > browser_config.MAX_DOWNLOAD_BYTES:
                dest.unlink(missing_ok=True)
                return self._fail(
                    task,
                    f"'{name}' was {size:,} bytes, over the "
                    f"{browser_config.MAX_DOWNLOAD_BYTES:,} limit. Deleted it.",
                    "download_too_large",
                )
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Downloaded '{name}' ({size:,} bytes) to {dest}.",
                data={"file": str(dest), "bytes": size},
            )
        except Exception as e:
            return self._fail(
                task,
                f"No download started from '{intent.text_arg}': {e}. That link "
                f"may just navigate rather than serve a file.",
                "download_failed",
            )

    def _save_pdf(self, task: Task, intent) -> AgentResult:
        """SENSITIVE -- writes a file. Chromium-only: Playwright's page.pdf()
        is not implemented for Firefox or WebKit, and requires headless mode.
        Reported honestly rather than producing an empty file."""
        guard = self._require_session(task)
        if guard:
            return guard
        try:
            os.makedirs(browser_config.PDF_DIR, exist_ok=True)
            page = self.session.page
            stem = self._safe_download_name(page.title() or "page") or "page"
            dest = resolve_safe_path(
                f"{stem}_{int(time.time())}.pdf", workspace_root=browser_config.PDF_DIR
            )
            page.pdf(path=str(dest), format="A4", print_background=True)
            size = dest.stat().st_size
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Saved the page as PDF ({size:,} bytes): {dest}",
                data={"file": str(dest), "bytes": size},
            )
        except Exception as e:
            return self._fail(
                task,
                f"Couldn't save the page as PDF: {e}. This needs Chromium in "
                f"headless mode -- Playwright doesn't implement page.pdf() "
                f"elsewhere.",
                "save_pdf_failed",
            )

    # ---- Bookmarks -------------------------------------------------------

    def _bookmark(self, task: Task, intent) -> AgentResult:
        """SAFE. Note honestly: these are SARVOS's own bookmarks in its
        SQLite store. They are NOT written to Chrome, Firefox, or any browser
        you actually use -- SARVOS drives a separate headless browser."""
        guard = self._require_session(task)
        if guard:
            return guard
        page = self.session.page
        url, title = page.url, (page.title() or "")
        self.memory.store.save_bookmark(intent.name_arg, url, title)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=(
                f"Bookmarked '{intent.name_arg}' -> {url}\n"
                f"(Saved in SARVOS's own store, not in your real browser.)"
            ),
            data={"name": intent.name_arg, "url": url},
        )

    def _list_bookmarks(self, task: Task, intent) -> AgentResult:
        marks = self.memory.store.all_bookmarks()
        if not marks:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="No bookmarks saved yet. Try 'bookmark this page as <name>'.",
                data={"bookmarks": []},
            )
        lines = "\n".join(f"  {m['name']}: {m['url']}" for m in marks)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"{len(marks)} bookmark(s):\n{lines}",
            data={"bookmarks": marks},
        )

    def _open_bookmark(self, task: Task, intent) -> AgentResult:
        mark = self.memory.store.get_bookmark(intent.name_arg)
        if mark is None:
            return self._fail(
                task,
                f"No bookmark called '{intent.name_arg}'. Try 'list bookmarks'.",
                "no_such_bookmark",
            )
        if not self.session.is_open():
            self.session.start()
        try:
            page = self.session.page
            page.goto(mark["url"], timeout=browser_config.PAGE_LOAD_TIMEOUT_MS)
            self._settle(page)
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Opened bookmark '{intent.name_arg}': {page.title()} ({mark['url']})",
                data={"name": intent.name_arg, "url": mark["url"]},
            )
        except Exception as e:
            return self._fail(task, f"Couldn't open '{mark['url']}': {e}", "open_failed")

    # ---- Snapshot compare (NOT monitoring) -------------------------------

    def _check_changes(self, task: Task, intent) -> AgentResult:
        """SAFE. Compares this page's text against the last snapshot taken.

        Deliberately NOT called monitoring. Monitoring means something runs
        while you're away; that needs a scheduler and a daemon, which is a
        Tier 8 concern and does not exist. This answers the honest question:
        "has it changed since I last looked?" -- and it answers it by
        comparing real text, not by asking a model to remember.
        """
        guard = self._require_session(task)
        if guard:
            return guard
        page = self.session.page
        url = page.url
        try:
            text = page.inner_text("body")
        except Exception as e:
            return self._fail(task, f"Couldn't read the page: {e}", "read_failed")

        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        previous = self.memory.store.get_page_snapshot(url)
        self.memory.store.save_page_snapshot(url, digest, len(text))

        if previous is None:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=(
                    f"No previous snapshot of {url}, so there's nothing to "
                    f"compare against. Saved one now ({len(text):,} chars). "
                    f"Ask again later to see what changed."
                ),
                data={"first_snapshot": True, "chars": len(text)},
            )

        if previous["text_hash"] == digest:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Unchanged since {previous['captured_at']} ({len(text):,} chars).",
                data={"changed": False, "since": previous["captured_at"]},
            )

        delta = len(text) - previous["char_count"]
        direction = "longer" if delta > 0 else "shorter"
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=(
                f"CHANGED since {previous['captured_at']}. The page text is "
                f"now {len(text):,} chars, {abs(delta):,} {direction} than "
                f"before. I can tell you it changed and by how much, not what "
                f"changed -- only a hash of the old text was kept, not the text."
            ),
            data={"changed": True, "since": previous["captured_at"],
                  "char_delta": delta},
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
            self._settle(self.session.page)
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
