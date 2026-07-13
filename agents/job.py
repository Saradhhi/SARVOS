"""
JobAgent -- an honest job-application assistant.

What it does NOT do: apply to a link for you. Real job portals (Workday,
Greenhouse, Lever, iCIMS) are JavaScript wizards with dynamic field names
and mandatory logins, and SARVOS stores no credentials by design. An agent
that claimed to "just apply" would pass a demo and fail silently on
applications attached to your name. So this does the tedious, safe,
reversible parts and leaves every irreversible act to you:

  - CANDIDATES: many people, each in their own folder. Set an active one
              ("use candidate alice") and every command after applies to
              them -- switch-and-go, so daily use isn't naming a candidate
              on every line. Scales to hundreds; one folder is one person's
              whole job search, backupable and deletable on its own.
  - PROFILE:  the active candidate's reusable application data.
  - POSTING:  save a job description, then honestly compare it to your
              resume -- what matches, what's missing, stated as the model's
              read of two real documents, not as fact about your fitness.
  - FILL:     map your profile onto a plain form's fields and PREVIEW it.
              The actual submit is the browser agent's gated SUBMIT, so you
              always see exactly what will be sent before it is.
  - TRACK:    a local record of what you applied to and when.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents import job_config as config
from agents.automation import resolve_safe_path
from agents.base import BaseAgent
from agents.job_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task
from llm.client import LLMUnavailable, get_llm_client

_MATCH_SYSTEM_PROMPT = (
    "You are comparing a job posting against a candidate's resume. Report, "
    "concretely: which required skills the resume clearly evidences, which "
    "the posting asks for but the resume does not mention, and any relevant "
    "experience the resume has that the posting doesn't ask about. Be "
    "specific and cite what you saw. Do NOT invent qualifications, do NOT "
    "guess at a fit score, and do NOT give encouragement -- this is a "
    "factual gap analysis the person will act on, not a pep talk. If either "
    "document seems truncated, say so."
)

# The user's own ATS-optimizer prompt, used verbatim as the system prompt for
# 'optimize my resume for <posting>'. One hard constraint is added at the end
# that the original leaves implicit: the model must not invent experience the
# resume doesn't contain. Everything this produces is analysis the person
# reviews, so it is SAFE, but a rewrite suggestion built on fabricated facts
# would be worse than useless -- it would be a lie the person might submit.
_OPTIMIZE_SYSTEM_PROMPT = (
    "You are an expert ATS resume optimizer and career strategist with 15+ "
    "years of experience helping candidates land roles at top companies.\n"
    "You are given a candidate's current resume and a target job description. "
    "Analyze both deeply and give the following in this exact order:\n"
    "1. ROLE FIT SCORE out of 10, with a 2-line reasoning on whether the "
    "candidate's domain genuinely matches this role.\n"
    "2. CORE PROBLEMS in the resume specific to this JD. List the top 5 gaps "
    "or misalignments.\n"
    "3. MISSING KEYWORDS the ATS will scan for. Pull these directly from the "
    "JD and say exactly where each should naturally fit in the resume.\n"
    "4. WEAK BULLET POINTS. Identify bullets that sound generic, lack metrics, "
    "or use passive voice. Show the original line and a rewrite using the "
    "X-Y-Z formula (accomplished X by doing Y, resulting in Z).\n"
    "5. RED FLAGS a recruiter or ATS might catch (employment gaps, vague "
    "titles, irrelevant experience taking too much space, formatting that "
    "breaks ATS parsing).\n"
    "6. SECTION-BY-SECTION VERDICT. For each section (summary, experience, "
    "skills, education, projects): keep, edit, or remove.\n\n"
    "CRITICAL HONESTY RULE: use ONLY facts, roles, dates, and skills that are "
    "actually present in the resume. Never invent experience, employers, "
    "metrics, or qualifications the candidate does not have. When you suggest "
    "adding a keyword, only suggest it where the candidate's real experience "
    "genuinely supports it; if it isn't supported, say so plainly rather than "
    "inventing a place for it. If the resume and the role are a poor domain "
    "match, say that directly in the fit score -- do not inflate it.\n\n"
    "End by noting that the person can now run 'rewrite my resume for "
    "<posting>' to generate a tailored version, or 'write a cover letter for "
    "<posting>'."
)

_REWRITE_SYSTEM_PROMPT = (
    "You are an expert ATS resume writer. Rewrite the candidate's resume to be "
    "tailored to the target job description and friendly to ATS parsers.\n"
    "Rules: simple formatting only -- no tables, no columns, no graphics. "
    "Standard section headings (Summary, Experience, Skills, Education, "
    "Projects). Weave in the JD's keywords NATURALLY.\n\n"
    "ABSOLUTE CONSTRAINT: every employer, title, date, degree, and metric must "
    "come from the candidate's real resume. You may re-order, re-word, "
    "re-emphasise, and surface relevant experience more prominently. You may "
    "NOT invent jobs, dates, numbers, certifications, or skills the candidate "
    "does not actually have. A resume with a fabricated fact is one the "
    "candidate could be fired for later -- accuracy is more important than "
    "impressiveness. If the candidate lacks something the JD wants, simply "
    "leave it out rather than inventing it. Output only the resume text."
)

_COVER_LETTER_SYSTEM_PROMPT = (
    "You are writing a concise, specific cover letter (about 250-350 words) "
    "for the candidate, for the target job, drawing only on their real "
    "resume. Connect their actual experience to the role's actual needs. No "
    "invented achievements, employers, or numbers. Avoid generic filler and "
    "cliches; be concrete about what in their background fits this job. If "
    "the fit is weak, write an honest letter that leads with genuine "
    "transferable strengths rather than overclaiming. Output only the letter."
)


class JobAgent(BaseAgent):
    name = AgentName.JOB

    def __init__(self, memory, browser=None):
        super().__init__(memory)
        # Optional live link to the interactive browser agent, wired in the
        # factory. When present, 'save this posting' reads the open page
        # directly and 'fill this form' inspects the real form fields --
        # turning separate commands into one workflow. When absent (tests,
        # standalone), both fall back to context or manual guidance.
        self.browser = browser

    def _live_page_text(self) -> str | None:
        """The open browser page's text, if a session is live. Read directly
        rather than trusting the model to have remembered it."""
        b = self.browser
        if b is None or not getattr(b, "session", None) or not b.session.is_open():
            return None
        try:
            return b.session.page.inner_text("body")
        except Exception:
            return None

    def _live_form_fields(self) -> list[str] | None:
        """The names/labels of fields on the open form, read from the real
        DOM. None if no live form -- never guessed."""
        b = self.browser
        if b is None or not getattr(b, "session", None) or not b.session.is_open():
            return None
        try:
            return b.session.page.evaluate(
                """() => Array.from(
                    document.querySelectorAll('input, textarea, select')
                ).filter(el => {
                    if (el.disabled || !(el.name || el.id)) return false;
                    const t = el.type || '';
                    return !['submit','button','reset','image','hidden'].includes(t);
                }).map(el => el.name || el.id || el.getAttribute('aria-label') || '')"""
            )
        except Exception:
            return None

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        op = intent.operation

        if op == Operation.ADD_CANDIDATE:
            return self._add_candidate(task, intent)
        if op == Operation.USE_CANDIDATE:
            return self._use_candidate(task, intent)
        if op == Operation.LIST_CANDIDATES:
            return self._list_candidates(task)
        if op == Operation.WHOAMI:
            return self._whoami(task)
        if op == Operation.SET_PROFILE:
            return self._set_profile(task, intent)
        if op == Operation.SHOW_PROFILE:
            return self._show_profile(task)
        if op == Operation.SAVE_POSTING:
            return self._save_posting(task, intent)
        if op == Operation.MATCH_POSTING:
            return self._match_posting(task, intent)
        if op == Operation.OPTIMIZE_RESUME:
            return self._optimize_resume(task, intent)
        if op == Operation.REWRITE_RESUME:
            return self._rewrite_resume(task, intent)
        if op == Operation.COVER_LETTER:
            return self._cover_letter(task, intent)
        if op == Operation.FILL_FORM:
            return self._fill_form(task, intent)
        if op == Operation.LOG_APPLICATION:
            return self._log_application(task, intent)
        if op == Operation.LIST_APPLICATIONS:
            return self._list_applications(task)

        return self._fail(
            task,
            f"I couldn't work out a job action from: '{task.instruction}'. Try "
            f"'add candidate <name>', 'use candidate <name>', 'list "
            f"candidates', 'set my email to ...', 'save this posting as "
            f"<name>', 'match <name> against the resume', 'log an application "
            f"to <company>', or 'list applications'.",
        )

    # ---- candidate management --------------------------------------------

    def _add_candidate(self, task: Task, intent) -> AgentResult:
        name = intent.candidate
        if not config.is_valid_candidate_name(name):
            return self._fail(
                task,
                f"'{name}' isn't a valid candidate name. Use letters, digits, "
                f"hyphens, or underscores (no spaces), up to 64 characters.",
                error="invalid_candidate_name",
            )
        d = self._candidate_dir(name)
        existed = d.is_dir()
        (d / "postings").mkdir(parents=True, exist_ok=True)
        self._set_active_candidate(name)
        verb = "Switched to existing" if existed else "Created"
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"{verb} candidate '{name}', now active. Put their resume in "
                   f"{d}, then set profile fields and start matching postings.",
            data={"candidate": name, "created": not existed},
        )

    def _use_candidate(self, task: Task, intent) -> AgentResult:
        name = intent.candidate
        if not config.is_valid_candidate_name(name):
            return self._fail(task, f"'{name}' isn't a valid candidate name.",
                              "invalid_candidate_name")
        if not self._candidate_dir(name).is_dir():
            return self._fail(
                task,
                f"No candidate called '{name}'. Create them with 'add candidate "
                f"{name}', or see who exists with 'list candidates'.",
                error="candidate_not_found",
            )
        self._set_active_candidate(name)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Active candidate is now '{name}'. Everything applies to them "
                   f"until you switch.",
            data={"candidate": name},
        )

    def _list_candidates(self, task: Task) -> AgentResult:
        root = Path(config.CANDIDATES_DIR)
        if not root.is_dir():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="No candidates yet. Create one with 'add candidate <name>'.",
                data={"candidates": []},
            )
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
        if not names:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="No candidates yet. Create one with 'add candidate <name>'.",
                data={"candidates": []},
            )
        active = self._active_candidate()
        lines = []
        for n in names:
            napps = len(self._load_applications(n))
            marker = " *" if n == active else "  "
            lines.append(f"{marker} {n} ({napps} application(s))")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"{len(names)} candidate(s) (* = active):\n" + "\n".join(lines),
            data={"candidates": names, "active": active},
        )

    def _whoami(self, task: Task) -> AgentResult:
        name = self._active_candidate()
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=(f"Active candidate: {name}" if name
                    else "No active candidate. Pick one with 'use candidate <name>'."),
            data={"candidate": name},
        )

    # ---- helpers ---------------------------------------------------------

    def _fail(self, task: Task, msg: str, error: str = "job_bad_request") -> AgentResult:
        return AgentResult(task_id=task.task_id, agent=self.name, success=False,
                           output=msg, error=error)

    # ---- candidate scoping ----------------------------------------------

    def _active_candidate(self) -> str | None:
        marker = Path(config.ACTIVE_CANDIDATE_FILE)
        if not marker.is_file():
            return None
        name = marker.read_text(encoding="utf-8").strip()
        return name or None

    def _set_active_candidate(self, name: str) -> None:
        marker = Path(config.ACTIVE_CANDIDATE_FILE)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(name, encoding="utf-8")

    def _candidate_dir(self, name: str) -> Path:
        return Path(config.CANDIDATES_DIR) / name

    def _require_candidate(self, task: Task):
        """Returns the active candidate name, or a failed AgentResult telling
        the person to pick one. Every profile/posting/application command
        needs a candidate -- without one there's no folder to read or write."""
        name = self._active_candidate()
        if name is None:
            return self._fail(
                task,
                "No active candidate. Pick one with 'use candidate <name>', or "
                "create one with 'add candidate <name>'. See them all with "
                "'list candidates'.",
                error="no_active_candidate",
            )
        if not self._candidate_dir(name).is_dir():
            return self._fail(
                task,
                f"The active candidate '{name}' has no folder anymore. Re-create "
                f"with 'add candidate {name}' or switch with 'use candidate "
                f"<name>'.",
                error="active_candidate_missing",
            )
        return name

    def _profile_path(self, candidate: str) -> Path:
        return self._candidate_dir(candidate) / "profile.json"

    def _postings_dir(self, candidate: str) -> Path:
        return self._candidate_dir(candidate) / "postings"

    def _applications_path(self, candidate: str) -> Path:
        return self._candidate_dir(candidate) / "applications.json"

    def _load_profile(self, candidate: str) -> dict:
        path = self._profile_path(candidate)
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_profile(self, candidate: str, data: dict) -> None:
        path = self._profile_path(candidate)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_applications(self, candidate: str) -> list:
        path = self._applications_path(candidate)
        if not path.is_file():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_applications(self, candidate: str, apps: list) -> None:
        path = self._applications_path(candidate)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(apps, indent=2), encoding="utf-8")

    # ---- profile ---------------------------------------------------------

    def _set_profile(self, task: Task, intent) -> AgentResult:
        field = intent.field
        # Accept a few natural aliases; otherwise require a known field so a
        # typo is caught rather than silently stored under a junk key.
        aliases = {
            "email_address": "email", "phone_number": "phone",
            "name": "full_name", "linkedin_url": "linkedin",
            "github_url": "github", "salary": "salary_expectation",
            "authorization": "work_authorization",
        }
        field = aliases.get(field, field)
        if field not in config.PROFILE_FIELDS:
            allowed = ", ".join(config.PROFILE_FIELDS)
            return self._fail(
                task,
                f"'{intent.field}' isn't a profile field I recognise. Known "
                f"fields: {allowed}.",
                error="unknown_profile_field",
            )
        # A profile is not a credential store. Refuse anything that smells
        # like a password outright -- see the module docstring.
        if field in ("password", "pwd", "secret"):
            return self._fail(
                task,
                "I won't store passwords. Log into sites yourself, in a "
                "browser you can see.",
                error="refused_credential",
            )
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        profile = self._load_profile(candidate)
        profile[field] = intent.value
        self._save_profile(candidate, profile)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Saved {field} = {intent.value} for {candidate}.",
            data={"candidate": candidate, "field": field, "value": intent.value},
        )

    def _show_profile(self, task: Task) -> AgentResult:
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        profile = self._load_profile(candidate)
        if not profile:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output="Your profile is empty. Set fields with e.g. 'set my "
                       "email to you@example.com'.",
                data={"profile": {}},
            )
        lines = "\n".join(f"  {k}: {v}" for k, v in profile.items())
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Profile for {candidate}:\n{lines}",
            data={"candidate": candidate, "profile": profile},
        )

    # ---- postings and matching -------------------------------------------

    def _save_posting(self, task: Task, intent) -> AgentResult:
        """Saves the CURRENT browser page's text as a posting, if a session
        is open; otherwise explains how to provide one. Deliberately reads
        from a real source rather than letting the model imagine a job."""
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        name = intent.value.replace(" ", "-")
        # Prefer the live open page; fall back to context, then explain.
        session_open = (
            self.browser is not None
            and getattr(self.browser, "session", None) is not None
            and self.browser.session.is_open()
        )
        text = self._live_page_text()
        if not text and task.context:
            text = task.context.get("page_text")
        postings_dir = str(self._postings_dir(candidate))

        if not text:
            if session_open:
                # A session IS open but the page gave no text -- almost always
                # a JS-heavy page that hasn't rendered. Don't tell them to open
                # a session they already have.
                return self._fail(
                    task,
                    "A browser session is open, but the page has no readable "
                    "text yet -- it's likely still rendering (common on "
                    "JavaScript-heavy job sites). Try 'read the page' to check, "
                    "and if it's still blank, restart with "
                    "SARVOS_WAIT_NETWORKIDLE=1 set so SARVOS waits longer for "
                    "content. Then 'save this posting as " + name + "' again.",
                    error="page_empty",
                )
            return self._fail(
                task,
                "I need the posting text. Open it in a browser session first "
                "('open a browser session at <url>'), then 'save this posting "
                f"as {name}' -- I'll read the open page. Or paste it into a file "
                f"in {postings_dir} named '{name}.txt'.",
                error="no_posting_text",
            )
        try:
            path = resolve_safe_path(f"{name}.txt", workspace_root=postings_dir)
        except Exception as e:
            return self._fail(task, f"Refusing to save '{name}': {e}", "unsafe_posting_path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Saved posting '{name}' ({len(text):,} chars). Match it "
                   f"with 'match {name} against my resume'.",
            data={"name": name, "chars": len(text)},
        )

    def _load_posting_and_resume(self, task: Task, candidate: str, posting_value: str,
                                 resume_field: str | None):
        """Shared by match/optimize/rewrite/cover-letter: load the real saved
        posting and the candidate's real resume. Returns (posting_text,
        resume_text, resume_name, name) or a failed AgentResult."""
        name = posting_value.replace(" ", "-")
        postings_dir = str(self._postings_dir(candidate))
        try:
            ppath = resolve_safe_path(f"{name}.txt", workspace_root=postings_dir)
        except Exception as e:
            return self._fail(task, f"Refusing to read '{name}': {e}", "unsafe_posting_path")
        if not ppath.is_file():
            return self._fail(
                task,
                f"No saved posting called '{name}'. Save one first with 'save "
                f"this posting as {name}'.",
                error="posting_not_found",
            )
        posting = ppath.read_text(encoding="utf-8", errors="replace")

        resume_path = self._find_resume(candidate, resume_field)
        resume_text = self._read_resume(task, candidate, resume_field)
        if isinstance(resume_text, AgentResult):
            return resume_text
        resume_name = resume_path.name if resume_path else "resume"
        return posting, resume_text, resume_name, name

    def _build_prompt(self, posting, resume_text, name, resume_name):
        p = posting[: config.MAX_POSTING_CHARS]
        r = resume_text[: config.MAX_POSTING_CHARS]
        trunc = len(posting) > config.MAX_POSTING_CHARS or len(resume_text) > config.MAX_POSTING_CHARS
        prompt = (
            f"--- JOB DESCRIPTION ({name}) ---\n{p}\n\n"
            f"--- CURRENT RESUME ({resume_name}) ---\n{r}"
        )
        return prompt, trunc

    def _optimize_resume(self, task: Task, intent) -> AgentResult:
        """SAFE. The deep ATS analysis (fit score, gaps, missing keywords,
        weak bullets, red flags, section verdicts). Reads the real resume and
        real posting; the system prompt forbids inventing experience."""
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        loaded = self._load_posting_and_resume(task, candidate, intent.value, intent.field)
        if isinstance(loaded, AgentResult):
            return loaded
        posting, resume_text, resume_name, name = loaded

        prompt, trunc = self._build_prompt(posting, resume_text, name, resume_name)
        try:
            analysis = get_llm_client().generate(prompt, system=_OPTIMIZE_SYSTEM_PROMPT)
        except LLMUnavailable as e:
            return self._fail(task, f"Can't analyze -- the LLM isn't available: {e}",
                              "llm_unavailable")
        warning = ("\n\n[WARNING: the posting or resume was truncated, so this "
                   "analysis is incomplete.]" if trunc else "")
        header = (f"ATS analysis of {resume_name} for '{name}' (the local model's "
                  f"read of your real resume and the real posting)")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"{header}:\n\n{analysis.strip()}{warning}",
            data={"candidate": candidate, "posting": name, "resume": resume_name,
                  "truncated": trunc},
        )

    def _rewrite_resume(self, task: Task, intent) -> AgentResult:
        """SENSITIVE -- writes a tailored resume file into the candidate's
        folder. Every fact must come from the real resume; the system prompt
        forbids fabrication. The person reviews the file before using it."""
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        loaded = self._load_posting_and_resume(task, candidate, intent.value, intent.field)
        if isinstance(loaded, AgentResult):
            return loaded
        posting, resume_text, resume_name, name = loaded

        prompt, trunc = self._build_prompt(posting, resume_text, name, resume_name)
        try:
            new_resume = get_llm_client().generate(prompt, system=_REWRITE_SYSTEM_PROMPT)
        except LLMUnavailable as e:
            return self._fail(task, f"Can't rewrite -- the LLM isn't available: {e}",
                              "llm_unavailable")

        out = self._candidate_dir(candidate) / f"resume_tailored_{name}.txt"
        out.write_text(new_resume.strip(), encoding="utf-8")
        note = ("\n\n(Note: source material was truncated, so parts of your "
                "resume may not be reflected -- check against the original.)"
                if trunc else "")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=(
                f"Wrote a tailored resume to {out}.\n\n"
                f"IMPORTANT: this was generated by the local model from your real "
                f"resume. Read it before you use it -- confirm every fact is "
                f"true and nothing was invented. It's a draft to edit, not a "
                f"finished document.{note}"
            ),
            data={"candidate": candidate, "posting": name, "file": str(out)},
        )

    def _cover_letter(self, task: Task, intent) -> AgentResult:
        """SENSITIVE -- writes a cover letter file. Same honesty constraint:
        only the candidate's real experience."""
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        loaded = self._load_posting_and_resume(task, candidate, intent.value, intent.field)
        if isinstance(loaded, AgentResult):
            return loaded
        posting, resume_text, resume_name, name = loaded

        prompt, trunc = self._build_prompt(posting, resume_text, name, resume_name)
        try:
            letter = get_llm_client().generate(prompt, system=_COVER_LETTER_SYSTEM_PROMPT)
        except LLMUnavailable as e:
            return self._fail(task, f"Can't write it -- the LLM isn't available: {e}",
                              "llm_unavailable")

        out = self._candidate_dir(candidate) / f"cover_letter_{name}.txt"
        out.write_text(letter.strip(), encoding="utf-8")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=(
                f"Wrote a cover letter to {out}.\n\n"
                f"Generated from {candidate}'s real resume by the local model. "
                f"Read and edit it before sending -- make sure it sounds like "
                f"you and every claim is true."
            ),
            data={"candidate": candidate, "posting": name, "file": str(out)},
        )

    def _match_posting(self, task: Task, intent) -> AgentResult:
        """Compares a saved posting against your resume via the LLM. The
        output is explicitly a gap analysis of two real documents, not a
        verdict on your worth or a fabricated fit score."""
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        # Posting.
        name = intent.value.replace(" ", "-")
        postings_dir = str(self._postings_dir(candidate))
        try:
            ppath = resolve_safe_path(f"{name}.txt", workspace_root=postings_dir)
        except Exception as e:
            return self._fail(task, f"Refusing to read '{name}': {e}", "unsafe_posting_path")
        if not ppath.is_file():
            return self._fail(
                task,
                f"No saved posting called '{name}'. Save one first with 'save "
                f"this posting as {name}'.",
                error="posting_not_found",
            )
        posting = ppath.read_text(encoding="utf-8", errors="replace")

        # Resume -- read from THIS candidate's own folder. If the command
        # named a file, use it; otherwise find the one resume in the folder.
        resume_path = self._find_resume(candidate, intent.field)
        resume_text = self._read_resume(task, candidate, intent.field)
        if isinstance(resume_text, AgentResult):
            return resume_text
        resume_name = resume_path.name if resume_path else "resume"

        p_trunc = len(posting) > config.MAX_POSTING_CHARS
        r_trunc = len(resume_text) > config.MAX_POSTING_CHARS
        prompt = (
            f"--- JOB POSTING ({name}) ---\n{posting[:config.MAX_POSTING_CHARS]}\n\n"
            f"--- RESUME ({resume_name}) ---\n{resume_text[:config.MAX_POSTING_CHARS]}"
        )

        try:
            analysis = get_llm_client().generate(prompt, system=_MATCH_SYSTEM_PROMPT)
        except LLMUnavailable as e:
            return self._fail(task, f"Can't match -- the LLM isn't available: {e}",
                              "llm_unavailable")

        warning = ""
        if p_trunc or r_trunc:
            which = " and ".join(
                w for w, t in (("the posting", p_trunc), ("your resume", r_trunc)) if t
            )
            warning = (
                f"\n\n[WARNING: {which} was truncated before analysis, so this "
                f"comparison is incomplete.]"
            )
        header = (
            f"Gap analysis: '{name}' vs {resume_name} (the local model's read "
            f"of both documents, not a verdict on your fit)"
        )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"{header}:\n\n{analysis.strip()}{warning}",
            data={"posting": name, "resume": resume_name,
                  "truncated": p_trunc or r_trunc},
        )

    _RESUME_EXTS = (".pdf", ".docx", ".txt", ".md")

    def _find_resume(self, candidate: str, named: str | None) -> Path | None:
        """The resume lives in the candidate's own folder. If a name was
        given, use it; otherwise pick the single resume-like file there, or
        None if it's ambiguous or absent.

        Files this agent GENERATES (resume_tailored_*, cover_letter_*) are
        excluded from auto-detection: after one rewrite the folder would
        otherwise hold two resume-like files and auto-detect would break."""
        cdir = self._candidate_dir(candidate)
        if named:
            p = cdir / named
            return p if p.is_file() else None
        candidates = [
            p for p in sorted(cdir.iterdir())
            if p.is_file() and p.suffix.lower() in self._RESUME_EXTS
            and p.name != "profile.json"
            and not p.name.startswith("resume_tailored_")
            and not p.name.startswith("cover_letter_")
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _read_resume(self, task: Task, candidate: str, named: str | None):
        """Read the candidate's resume, reusing the document agent's
        extraction. Returns text, or a failed AgentResult."""
        from agents.document import DocumentAgent

        # Guard against a named file escaping the candidate folder.
        if named:
            try:
                resolve_safe_path(named, workspace_root=str(self._candidate_dir(candidate)))
            except Exception as e:
                return self._fail(task, f"Refusing to read '{named}': {e}.",
                                  "unsafe_resume_path")

        path = self._find_resume(candidate, named)
        if path is None:
            cdir = self._candidate_dir(candidate)
            if named:
                return self._fail(
                    task, f"'{named}' isn't in {cdir}.", "resume_not_found")
            return self._fail(
                task,
                f"Couldn't find a single resume in {cdir}. Put one there (pdf, "
                f"docx, or txt), or name it: 'match <posting> against the resume "
                f"<filename>'.",
                "resume_not_found",
            )
        doc = DocumentAgent(self.memory)
        try:
            return doc._extract(path)
        except Exception as e:
            return self._fail(task, f"Couldn't read '{path.name}': {e}",
                              "resume_read_failed")

    # ---- form fill -------------------------------------------------------

    def _fill_form(self, task: Task, intent) -> AgentResult:
        """Maps profile fields onto the current form and reports what it would
        type -- it does NOT submit. This is guidance for the browser agent's
        type/preview/submit flow, which is where the gated, irreversible act
        lives. Kept separate on purpose: the judgment about whether a mapping
        is right stays with you."""
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        profile = self._load_profile(candidate)
        if not profile:
            return self._fail(
                task,
                f"{candidate}'s profile is empty, so there's nothing to fill "
                f"from. Set fields first, e.g. 'set my email to "
                f"you@example.com'.",
                error="empty_profile",
            )
        # Prefer the REAL fields on the open form; fall back to context.
        fields = self._live_form_fields()
        if not fields and task.context:
            fields = task.context.get("form_fields")

        if not fields:
            # No live form -- give the person the commands to run, honestly
            # flagged as needing review because real field names vary.
            lines = "\n".join(
                f"  type \"{v}\" into the {k} field" for k, v in profile.items()
            )
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=(
                    "No live form open (open one in a browser session first). "
                    f"From {candidate}'s profile, these are the commands you'd "
                    "run -- review each before sending, since field names on "
                    "real sites rarely match exactly:\n\n"
                    + lines +
                    "\n\nThen 'preview the form' to see what will be submitted, "
                    "and 'submit' (you'll be asked to confirm)."
                ),
                data={"candidate": candidate, "suggested": profile},
            )

        # A live form: map profile values onto the REAL field names by
        # best-effort overlap, and emit the exact type commands. The person
        # still reviews and runs them -- SARVOS never types or submits here.
        mapping, unmatched = {}, []
        for fname in fields:
            key = (fname or "").lower().replace(" ", "_")
            hit = None
            for pk, pv in profile.items():
                if pk in key or key in pk or any(
                    part in key for part in pk.split("_")
                ):
                    hit = (pk, pv)
                    break
            if hit:
                mapping[fname] = hit[1]
            else:
                unmatched.append(fname)

        if not mapping:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=(
                    f"The open form has fields {fields}, but none clearly match "
                    f"{candidate}'s profile keys. Fill it manually with 'type "
                    f"\"<value>\" into the <field> field'."
                ),
                data={"candidate": candidate, "form_fields": fields, "mapping": {}},
            )

        cmds = "\n".join(f"  type \"{v}\" into the {f} field" for f, v in mapping.items())
        note = ""
        if unmatched:
            note = (
                f"\n\nNo profile match for these fields (fill them yourself): "
                f"{', '.join(unmatched)}."
            )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=(
                f"Matched {len(mapping)} of {len(fields)} form field(s) to "
                f"{candidate}'s profile. Review, then run:\n\n{cmds}{note}\n\n"
                f"Then 'preview the form' and 'submit' (you'll confirm)."
            ),
            data={"candidate": candidate, "mapping": mapping, "unmatched": unmatched},
        )

    # ---- tracking --------------------------------------------------------

    def _log_application(self, task: Task, intent) -> AgentResult:
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        raw = intent.value.strip()
        company, role = raw, ""
        for sep in (" - ", " \u2013 ", ": ", ", "):
            if sep in raw:
                company, role = raw.split(sep, 1)
                break
        from datetime import datetime, timezone
        apps = self._load_applications(candidate)
        app_id = (max((a.get("id", 0) for a in apps), default=0)) + 1
        apps.append({
            "id": app_id, "company": company.strip(), "role": role.strip(),
            "status": "applied", "applied_at": datetime.now(timezone.utc).isoformat(),
        })
        self._save_applications(candidate, apps)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Logged application #{app_id} for {candidate}: "
                   f"{company.strip()}" + (f" -- {role.strip()}" if role.strip() else ""),
            data={"candidate": candidate, "id": app_id,
                  "company": company.strip(), "role": role.strip()},
        )

    def _list_applications(self, task: Task) -> AgentResult:
        candidate = self._require_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate
        apps = sorted(self._load_applications(candidate),
                      key=lambda a: a.get("applied_at", ""), reverse=True)
        if not apps:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"No applications logged for {candidate} yet. Log one "
                       f"with 'log an application to <company>'.",
                data={"applications": []},
            )
        lines = []
        for a in apps:
            when = a["applied_at"][:10]
            role = f" -- {a['role']}" if a["role"] else ""
            lines.append(f"  #{a['id']} {a['company']}{role} ({a['status']}, {when})")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"{len(apps)} application(s):\n" + "\n".join(lines),
            data={"applications": apps},
        )

