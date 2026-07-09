# SARVOS — Phase 1a Foundation

A working implementation of the core loop from the SARVOS spec: user input →
Planner → routed agent → memory read/write → confirmation gating on risky
actions → audit log. No voice, no UI, no automation yet — this is the piece
everything else gets built on top of.

## Run it

**Optional but recommended — set up the free local LLM (Ollama):**
```bash
# Install: see https://ollama.com/download for your OS
ollama serve                 # starts the local server (localhost:11434)
ollama pull llama3.2         # one-time download of a small, fast model
```
If you skip this, SARVOS still runs — the Coding and General agents will
tell you plainly that the local model isn't reachable and how to start it,
instead of crashing or faking a response.

```bash
pip install -r requirements.txt
```

**Option A — CLI:**
```bash
python main.py
```

**Option B — Web UI (browser):**
```bash
uvicorn api.server:app --reload
```
Then open http://localhost:8000.

**Option C — Desktop app (native window, no browser chrome):**
```bash
python desktop.py
```
Opens the exact same UI/backend as Option B, but in its own OS window via
[pywebview](https://pywebview.flowrite.com/) instead of a browser tab — no
address bar, no tabs. This is a pragmatic middle step before a full
Electron/Tauri build (which the original spec names as the eventual
target): same backend, same HTML, just a different window. Moving to
Electron/Tauri later doesn't require rewriting either.

**Option D — Voice (wake word + speech in/out):**
```bash
pip install -r requirements-voice.txt
python -m voice.assistant
```

**Read this before using it — real limitations, not caveats-for-show:**

- **The wake phrase is "Hey Jarvis," not "Hey SARVOS."** openWakeWord (the
  free/offline wake-word engine used here) ships a fixed set of pretrained
  models: `alexa`, `hey_jarvis`, `hey_mycroft`, `timer`, `weather`. It does
  NOT include "Hey SARVOS" — training a genuinely custom wake word is a
  separate ML project (synthetic data generation + training pipeline), out
  of scope here. `hey_jarvis` is the closest thematic stand-in and is the
  default (`SARVOS_WAKE_WORD` env var to change it to another bundled
  option).
- **Silence detection is a simple RMS energy threshold, not a trained VAD.**
  Good enough in a quiet room; will likely cut off speech too early, or not
  end recording promptly, in a noisy environment. Tune
  `SARVOS_SPEECH_RMS_THRESHOLD` / `SARVOS_SILENCE_SECONDS` if needed.
- **TTS uses your OS's built-in voice** (Windows SAPI5) via pyttsx3, not a
  higher-quality model like Piper — chosen deliberately to avoid extra
  setup (downloading a platform binary + voice model files) for a first
  working version. If no TTS engine is available at all, SARVOS degrades
  to printing text instead of crashing (verified: this sandbox has no
  espeak installed on Linux, and the fallback path works exactly as
  designed).
- **This could only be partially tested before reaching you.** The
  sandbox this was built in has no microphone or speaker at all — not even
  a way to simulate one. What WAS verified here, against the real
  libraries (not mocked): the wake-word model actually loads and runs
  inference correctly (caught and fixed a real bug — the constructor
  argument name was wrong until tested against the actual installed
  library), TTS's graceful fallback when no engine exists, STT's silence
  short-circuit avoiding an unnecessary model load, and the full
  confirmation-flow conversation logic (yes/no handling, ambiguous
  response handling, pending-state blocking) — all with real code, no
  audio hardware needed for that part. What could NOT be verified here:
  whether the wake word actually triggers reliably on your voice, mic
  levels, and end-to-end audio quality. That needs your machine.

**How it works:** say "Hey Jarvis," wait for it to respond, then speak your
request. It'll ask "yes" or "no" out loud for anything destructive (same
confirmation gate as CLI/web/desktop — all four interfaces share the exact
same orchestrator via `core/factory.py`). Ctrl+C to stop.

Try:
```
remember that I prefer dark mode
what do you know about my preferences
debug this function
delete all my files        <- triggers a confirmation prompt (y/n)
log                          <- shows the audit trail
```

## Automation (real file operations + git, sandboxed)

The first agent in this build with REAL effects, not just generated text.
Available from CLI, web UI, desktop app, and voice — all four share the
same orchestrator via `core/factory.py`.

**Try it:**
```
write a file called todo.txt with buy milk and eggs
read file todo.txt
list the files in .
delete the file todo.txt        <- triggers the confirmation flow, for real this time
git status
git log
```

**Safety model, layered:**
1. **Sandboxed workspace.** All file read/write/list/delete operations are
   resolved against `SARVOS_WORKSPACE_ROOT` (default: `./sarvos_workspace`,
   NOT your home directory or Desktop) and REFUSED if the resolved path
   would escape it — blocks both `../../etc/passwd`-style traversal and
   absolute paths pointing elsewhere. Enforced at path-resolution time,
   not just hoped for from instruction parsing.
2. **Git allowlist.** Only specific subcommands can run at all:
   `status`/`log`/`diff`/`branch`/`show`/`remote` (safe), `add`/`commit`/
   `fetch`/`stash` (sensitive), `push`/`pull`/`checkout`/`reset`/`merge`/
   `rebase` (destructive). Anything else is refused outright — this is an
   allowlist, not a denylist. Checked twice: once by the Planner (for risk
   classification) and again by the agent itself right before executing
   (defense in depth).
3. **The same confirmation gate as everything else.** SENSITIVE/DESTRUCTIVE
   operations go through the orchestrator's central confirmation check
   before this agent ever runs — verified end-to-end
   (`tests/test_automation_e2e.py`): a destructive delete request raises
   `PendingConfirmation`, the file is confirmed NOT deleted yet, and only
   after explicit approval does it actually disappear. Rejecting leaves
   it untouched. This is the fix for the "I've cleared your history"
   problem from earlier testing — that was a text response with nothing
   behind it; this has real effects, gated the same way.
4. **Size/timeout caps**: file reads over `SARVOS_MAX_FILE_SIZE_BYTES`
   (default 1MB) are refused; git commands timeout after
   `SARVOS_GIT_TIMEOUT_SECONDS` (default 15s) rather than hanging forever.

**Deliberately NOT an LLM freely deciding what shell commands to run** —
that would be a serious, hard-to-bound risk for a feature with real
filesystem/subprocess effects. Every operation is explicit, enumerated,
and matched via deterministic pattern parsing
(`agents/automation_intent.py`). Doesn't match a known pattern? SARVOS
says so and suggests valid phrasing, rather than guessing.

**A real bug caught during testing, worth knowing about:** the path-safety
function originally took `workspace_root` as a default parameter bound to
`WORKSPACE_ROOT` at import time. Monkeypatching that value in tests
silently did nothing — Python evaluates default arguments once, at
function definition — so every file operation was quietly running against
the real default directory instead of the test's temp workspace, and a
stray `sarvos_workspace/` folder with real files in it was the physical
evidence. Fixed by looking up the config value dynamically at call time
instead of via a stale default. Worth knowing if you extend this code:
reference `automation_config.SOMETHING` at the point of use, not as an
imported bare name or default parameter.

**Not yet built**: IDE integration and a general workflow engine — each a
substantial separate project. This automation agent is the foundation
those would eventually call into, not a replacement for building them.

## Browser automation (real Playwright, read-only)

```
open website github.com
take a screenshot of example.com
```

Real headless-Chromium navigation via Playwright — extracts the page
title and visible text (capped at `SARVOS_MAX_PAGE_TEXT_LENGTH`, default
3000 chars), or saves a real screenshot to `sarvos_workspace/screenshots/`.

**Scope, deliberately narrow**: read-only browsing only. NOT included:
form filling/submission, login, downloads, or multi-step flows — those
have real side effects on external sites and deserve their own
separately-scoped, separately-tested work.

**Safety**: only `http://` and `https://` URLs are accepted. `file://`,
`javascript:`, `data:`, `mailto:`, and other schemes are explicitly
refused — this closes off using "open a website" as a backdoor into
reading local files or executing script URIs. **A real bug was caught and
fixed here during testing**: the first version of the scheme check
required `://` to detect a scheme at all, which `javascript:alert(1)`
doesn't have — it was being silently treated as scheme-less and getting
`https://` prepended to it (`https://javascript:alert(1)`), which then
WOULD have passed the "is this http(s)?" check, completely defeating the
safety filter. A test actually failing, not advance reasoning, is what
caught this.

Tested against a real local HTTP server (no external network dependency
for the test suite) with real Playwright navigation, a real screenshot
file written and verified non-empty, and confirmed live against actual
external sites (github.com) during manual testing.

## Interactive browsing (stateful: forms, logins, clicking)

```
open a browser session at example.com
type "myusername" into the username field
type "mypassword" into the password field
click the accept cookies button
read the page
submit          (or: log in / sign in)
close the browser session
```

Distinct from the read-only browser agent above — this one holds a
**persistent** Playwright session open across turns, because interactive
browsing is inherently multi-step and stateful: you open a page, *then*
type into a field on it, *then* submit, all needing the same live browser
to still be open. The session lives as instance state on the agent (which
the orchestrator keeps alive for its whole lifetime) and survives between
turns until you explicitly `close the browser session`.

**Confirmation gating**: `type`, `click`, `read`, and `open` are `SAFE`
(nothing permanent changes). `submit` / `log in` / `sign in` are
`DESTRUCTIVE` — gated by the same central confirmation check as
everything else, because that's the moment real, often irreversible side
effects happen (authenticating, sending data). Verified end-to-end
against a real local HTML form: `submit` raises `PendingConfirmation` and
the form is confirmed to *not have navigated* at that point; only after
explicit approval does the real form actually submit
(`tests/test_interactive_browser_agent.py::test_submit_is_gated_and_executes_real_form_after_approval`).
This matches an explicit decision to gate only the moment of consequence,
not every harmless click.

**No stored credentials, ever** — by design there is no operation to
save, load, or auto-fill passwords. Logging in means *you* type your
credentials in the moment via a normal `type` command; nothing is
persisted, and typed values are never echoed back in responses (they
might be passwords). **Honest security caveat, stated plainly**: typing
credentials via this agent means typing them into a *headless, invisible*
automated browser session against a real site — a genuinely worse
security position than typing into your own visible browser, since you're
trusting the automation with live credentials in the moment. Nothing is
stored, but that in-the-moment trust is real; use it accordingly.

**Field/element matching** is best-effort by common stable attributes
(placeholder, name, id, label, aria-label, visible text). It's not a full
accessibility-tree resolver — if a match is ambiguous or missing, the
agent says so clearly (`read the page` to see what's there) rather than
guessing and clicking the wrong thing.

**A real bug found from live use on DuckDuckGo, then fixed**: modern
search sites often have a submit `<button>` that's deliberately `disabled`
and hidden — the search actually fires from a JavaScript handler when you
press Enter, and the button is decorative. The first version found that
button and timed out trying to click the unclickable thing, never
reaching its Enter-key fallback ("a submit button exists" isn't "a
*clickable* submit button exists"). Fixed to require the button be
genuinely visible and enabled before clicking, otherwise focus the text
field and press Enter — which is how real search boxes submit. Covered by
a regression test modeling exactly this (a disabled/hidden button plus a
JS keydown handler), after confirming empirically that Chromium genuinely
won't natively submit a form whose only submit control is disabled.

## Window management (list, focus, move, resize, close)

```
list windows
what's the active window
focus notepad          /  switch to chrome
minimize               (bare verb = the active window)
minimize notepad       /  maximize notepad  /  restore notepad
move notepad to 100, 200
resize notepad to 800x600
close the notepad window
```

**Risk tiers**: `list`/`active` are `SAFE`. `focus`/`minimize`/`maximize`/
`restore`/`move`/`resize` are `SENSITIVE` — real visible effects, but
trivially reversible by the person at the keyboard. `close` is
`DESTRUCTIVE`: it can lose unsaved work, so it goes through the same
central confirmation gate as everything else, verified end-to-end (the
window is confirmed untouched at the moment `PendingConfirmation` is
raised, and closes only after approval).

**Ambiguity is refused, not guessed.** If `close the word window` matches
two Word documents, the agent lists them and stops. Silently minimizing the
wrong window is an annoyance; silently *closing* the wrong one loses work.

**Routing**: checked before Computer Control, deliberately. That agent owns
`close the application notepad` (terminating a process); this one owns
`close the notepad window` (closing one window). Both remain `DESTRUCTIVE`,
and a test asserts neither steals the other's phrasing.

**The verb-anchoring problem, caught by its own tests**: `focus`,
`minimize`, `restore`, `move`, and `close` are all extremely common in
ordinary speech. The first draft happily matched *"focus on your work"*,
*"minimize the risk"*, and *"restore my faith in humanity"*. Each verb is
now anchored — it needs an explicit window noun, a single bare title token,
or nothing at all (meaning the active window). Same lesson as the `develop`
substring bug and the `show me` misrouting, and it keeps recurring because
natural language reuses these words constantly.

**The honest limitation, worse here than anywhere else in this project**:
`pygetwindow` does not merely fail on Linux — it raises `NotImplementedError`
on *import*. Not one real window operation could be executed while writing
this. Every Windows call therefore lives behind `WindowBackend`, a seam
containing no logic, and all 20 agent tests run against a substitute. What
is genuinely verified: routing, risk tiers, title matching, ambiguity
refusal, error handling, and the confirmation gate. What is **not** verified
anywhere: that `pygetwindow`'s own methods do what their names say on real
Windows. Given that this project's Windows testing has already caught a
pycaw API change, a battery data-shape bug, and a near-miss real shutdown —
all invisible to the sandbox — that gap deserves stating plainly rather
than glossing over. The lazy import is itself load-bearing: without it, this
module would crash the entire agent registry at startup on any non-Windows
machine (asserted by a test).

## Computer control agent (screenshot, clipboard, volume, power state)

```
take a screenshot
read the clipboard
copy 'hello world' to the clipboard
mute / unmute / turn the volume up / set the volume to 50%
increase the brightness / set the brightness to 30%
lock my computer
launch notepad
close the application notepad
shut down my computer / restart my computer / put my computer to sleep
```

**Scope decision, same reasoning as Terminal**: no keyboard/mouse
simulation or hotkey automation. "Simulate any keystroke or click" is an
unbounded capability — fundamentally different from every allowlisted
action elsewhere in this project, since it could type or click *anything,
in any application*. Window resize/move/minimize and screen/mic recording
are also deferred — real, separate pieces of work.

**Risk tiers**: screenshot, clipboard, and locking are `SAFE` (fully
reversible, no data-loss risk). Volume, brightness, and launching an app
are `SENSITIVE` (real effects, easily undone). Closing an app, shutdown,
restart, and sleep are `DESTRUCTIVE` — gated by the same central
confirmation check as everything else, verified end-to-end the same way
as AutoDeveloper's deploy command: a monkeypatched shutdown call is
confirmed to **not execute** at the moment `PendingConfirmation` is
raised, and only runs after explicit approval
(`tests/test_computer_control_agent.py::test_shutdown_never_executes_before_confirmation`).

**A real safety bug found and fixed during testing, not hypothesized in
advance**: `close_app` matches running processes by a case-insensitive
substring against their name. The very first test for this used
`sys.executable`'s name ("python3") as the target — and since the test
suite itself runs *as* a `python3` process, that call matched and
terminated the **pytest runner's own process**, hanging the test run
entirely. Fixed by excluding SARVOS's own process ID from ever being
matched, regardless of how generic the requested name is — a real
protection against SARVOS accidentally terminating itself, not just a
test-only fix. Matching was also extended to check each process's full
command line, not just its bare name — lets a request target one
specific process precisely instead of every process sharing a generic
interpreter name, and made the test itself safer (a unique marker string
can't collide with anything else genuinely running on the machine).

**A serious near-miss, found from real Windows testing, fixed
immediately**: a test (`test_shutdown_not_supported_on_non_windows`)
assumed it would only ever run in a non-Windows sandbox and therefore
never reach real execution. Run on an actual Windows machine, it fell
straight through to a **real, unmocked `shutdown /s /t 0` call** — the
machine only failed to actually shut down because the screen happened to
be locked at that exact moment (Windows refuses a remote/API shutdown
while locked without a force flag), not because of any protection in the
code. That was luck, not safety. Fixed two ways: (1) the test itself is
now properly platform-conditional, skipped entirely on Windows rather
than assuming the platform; (2) a permanent defense-in-depth guard was
added directly to `_execute_system_command` — it refuses to proceed if
`PYTEST_CURRENT_TEST` (an environment variable pytest sets for the
duration of every test) is set, so a future test that forgets to mock
this method fails loudly and safely instead of silently attempting a
real, irreversible system command.

**Real cross-platform test bugs found from the same Windows run**: `lock`
and `brightness` were asserted to always fail in tests (written and
verified only in this sandbox's headless Linux environment) — but on a
real Windows laptop with a real display, both genuinely work,
correctly, as designed. The tests wrongly assumed failure; fixed to
accept whichever outcome is actually true for the machine running them.
Separately, `launch`/`close` tests originally used the Unix `sleep`
command, which doesn't exist on Windows at all (a real
`FileNotFoundError` on the actual test run) — fixed to use
`sys.executable`, which works identically on both platforms.

**Honest limitations, verified directly rather than assumed**: this was
built and tested in a headless Linux container with no display, no
clipboard mechanism, and no Windows COM interfaces at all. Screenshot,
clipboard, brightness, and volume all correctly report a clear failure
here rather than crashing (the same graceful-degradation pattern as
System Info's no-battery handling) — genuine real-world use of these on
a real Windows desktop, with a real screen and real audio device, needs
verification on that actual hardware. `pycaw` (Windows volume control)
is only installed on Windows at all (`sys_platform == 'win32'` in
`requirements.txt`) since its own internals fail to *import* on Linux
(`ctypes.HRESULT` doesn't exist outside Windows) — confirmed directly,
not assumed.

## Terminal agent (real diagnostics, not shell execution)

```
show me the running processes
whoami
what's my hostname
what os version am I running
```

Deliberately NOT "run any command the user describes" — arbitrary shell
execution driven by natural language is a fundamentally different risk
category than every other agent's allowlist here (there's no realistic
way to allowlist "anything a user might phrase as a command"). Instead,
this covers a fixed, small set of real diagnostics, each backed by a
direct Python library call (`psutil`, `getpass`, `socket`, `platform`)
rather than a subprocess shell-out — strictly safer AND more reliable/
cross-platform than parsing `tasklist`/`whoami`/`hostname`/`ver` output
would be.

## AutoDeveloper agent (reviewed and rebuilt from an external integration)

```
analyze the workspace
run the tests
deploy the project
```

This one has a real story worth documenting honestly. An external
integration was proposed that connected a separate "AutoDeveloper" tool
into SARVOS. On review, it had two genuine, serious problems, not just
rough edges:

1. **The confirmation prompt didn't actually gate anything.** The
   proposed wrapper called `wrapper.execute(task)` — which ran the real
   pipeline (subprocess test execution, file writes) — *before* returning
   a "[GATED] ... Awaiting user verification [y/n]" string. That string
   was just text handed to the Coding agent to talk about; typing "y"
   afterward didn't resume anything, because the action had already
   happened. This is the opposite of how confirmation gating works
   everywhere else in SARVOS (see `core/orchestrator.py`'s central
   DESTRUCTIVE check, which runs *before* any agent's `handle()` is ever
   called).
2. **Weak path safety.** File read/write used
   `safe_path.startswith(os.path.abspath(workspace))` — a sibling
   directory like `workspace_evil/` also satisfies that string check
   without being inside `workspace/` at all. `agents/automation.py`'s
   `resolve_safe_path()` avoids exactly this bug via proper
   parent-directory membership, not string-prefix matching.
3. Also present: routing on a bare `if 'develop' in text.lower()`
   substring check, which would misfire on completely ordinary sentences
   ("let's develop this idea further"); and an automatic "self-healing"
   loop that called a hardcoded stub (`simulate_llm_patch`, not a real
   patch generator) and wrote its fake output directly to a test file,
   automatically, before any confirmation at all.

**Rebuilt properly**, matching every other agent's pattern:
- `agents/autodeveloper_intent.py`: specific phrase patterns, not
  substring matching — covered by an explicit negative-case test using
  the exact sentences that would have broken the original routing.
- `RUN_TESTS` and `DEPLOY` are both `DESTRUCTIVE`, gated by the
  orchestrator's real, central confirmation check *before* this agent's
  `handle()` ever runs for those operations — verified with an actual
  end-to-end test through the real orchestrator: a deploy command that
  creates a marker file is confirmed to **not have run yet** at the
  point `PendingConfirmation` is raised, and only runs after explicit
  approval (`tests/test_autodeveloper_agent.py::test_deploy_command_never_runs_before_confirmation`).
- File/path safety fixed by using the same tested `resolve_safe_path()`
  already used by `agents/automation.py` — no user-supplied paths are
  accepted anywhere in this agent at all; every operation works against
  a fixed, admin-configured workspace root instead.
- The automatic fake-patch-writing loop was **not** carried over. It has
  since been rebuilt properly — see below.

### Auto-heal: propose a fix, review the diff, then apply

```
run the tests                    # see it fail
propose a fix                    # recommends a file, writes nothing
propose a fix for calc.py        # real LLM patch + real unified diff
apply the fix                    # DESTRUCTIVE -- gated, then written
```

The original integration's "self-healing" was a `simulate_llm_patch` stub
that returned a hardcoded fake test, wrote it to disk automatically, and
did so before any confirmation. All three of those properties are
deliberately inverted here:

1. **The LLM call is real** — the actual failing test output and the
   actual file contents go to Ollama (`llm/client.py`), and it degrades
   gracefully if Ollama isn't running.
2. **`propose a fix` writes nothing.** It is `SAFE`, holds the proposed
   patch in memory only (never on disk), and shows you a real unified
   diff generated with `difflib`.
3. **`apply the fix` is `DESTRUCTIVE`** — gated by the orchestrator's real
   confirmation check *before* anything is written, and it can only apply
   a patch you have already seen. There is no automatic heal loop.

**A real, fundamental limitation — found by running pytest for real
rather than assuming**: default pytest output often contains **no
reference to the buggy source file at all**. For `assert add(2, 2) == 4`
failing, the output names `test_calc.py` but never `calc.py`. So which
source file is wrong genuinely cannot be determined from test output in
general.

An early version tried to infer it, and its own test caught the
consequence: it selected the *test* file (the only path in the output)
and would have overwritten the test with the fix meant for the source —
**precisely** how the original stub clobbered a test file. Rather than
paper over that with better regexes, the agent now **recommends** a
target and stops; you confirm it by naming the file explicitly. Test
files are refused as patch targets outright, even on direct request,
since making a failing test pass by rewriting the test is almost never
the fix. Additional guards: paths are resolved through the same tested
`resolve_safe_path()`, oversized files are refused rather than silently
truncated (a model patching content it never fully saw is a genuinely
dangerous failure mode), markdown code fences are stripped from the
model's response, and a patch is discarded if the file changed between
proposal and approval — the diff you approved would no longer describe
reality.

### Two guards added after the LLM fabricated changes that never happened

Live testing of auto-heal surfaced something worth documenting, because it
is exactly the failure the propose/diff/apply design exists to prevent —
and it happened twice in one session:

1. After `propose a fix` (a `SAFE` operation that asks nothing), the user
   typed `y` out of habit. With no pending confirmation, that fell through
   to the general chat agent, which improvised confident prose: *"Looks
   good! ... Applying the changes..."* followed by a diff-shaped block.
   **Nothing had been written.** The model was roleplaying.
2. Moments later, a shell command (`type calc.py`) typed at the SARVOS
   prompt reached the same agent, which fabricated a complete "Before /
   After" listing of the file and stated it *"has been updated"* — with
   invented explanatory comments. The file was untouched; the model has no
   filesystem access and simply pattern-matched the conversation.

Both are now guarded:

- **`main.py`** intercepts a bare `y`/`yes`/`n`/`no` when nothing is
  pending and replies plainly, never invoking the LLM. A real message that
  merely contains those words (*"no idea what that means"*) still passes
  through — covered by an explicit negative-case test.
- **The general agent's system prompt** (both text and voice) now states
  categorically that it has no access to files, filesystem, or terminal;
  must never invent file contents; must never show a before/after block
  describing the user's real files; and must never claim a change was
  applied. Asserted directly in `tests/test_stray_confirmation.py`.

**Then the prompt guard itself failed, which is the most useful result of
all.** Asked *"what's in calc.py"*, the model correctly said *"You can't
see the contents of calc.py"* — and then displayed a diff of that exact
file anyway, in direct violation of the instruction it had just followed.
The diff was reconstructed from conversation; it even had the indentation
wrong. Plausible, and false.

The conclusion is not "write a firmer prompt." It's that **a system prompt
is a request, not a constraint** — the model can be pulled off it by the
conversational gravity of being helpful. So the rule is now enforced in
code, where it can't be argued with: `strip_fabricated_diffs()` removes
any diff-shaped block from general-agent output and replaces it with an
explicit notice saying why. Ordinary code examples are untouched — the
agent may answer coding questions freely; it just may never present the
contents of your real files. The regression test uses the model's verbatim
real output.

The underlying lesson generalizes past this one bug: **an LLM narrating an
action is not evidence the action occurred.** The only thing that made the
difference here was checking the real file at the shell — and the fact
that the actual write path is gated, so nothing *could* have been written
without an explicit approval the user never gave. Prompts shape behavior;
only code constrains it.

## System info agent (real CPU/RAM/disk/battery/network stats)

```
system info
check my cpu
how much ram do I have
check my disk usage
check my battery
what's my network status
```

The fourth real-capability agent (after Automation, Browser, Research),
and the simplest to make fully real and fully tested: entirely read-only
(every operation is SAFE, no confirmation gating at all), no network
dependency, works identically in any environment. Uses `psutil` for real
CPU/RAM/disk/battery/network queries.

Handles the no-battery case honestly rather than assuming a laptop: this
was built and tested in a headless Linux container with no battery at
all, and correctly reports "No battery detected -- this looks like a
desktop system" rather than erroring or faking a number.

## Research agent (real web search)

```
research the history of the internet
search for the best sourdough bread recipe
look up quantum computing basics
find information about climate change
```

The third real-capability agent (after Automation and Browser), built to
prove the agent protocol generalizes cleanly to a new capability rather
than being special-cased for the first two -- `agents/research_intent.py`
for deterministic instruction classification (shared with the Planner,
same as automation/browser).

**This one went through two real pivots before landing somewhere
sustainable, each driven by actual live-testing feedback, not guessed in
advance:**

1. **First version**: Playwright navigating DuckDuckGo's unofficial,
   no-JS HTML results page (`html.duckduckgo.com`), matching
   BrowserAgent's pattern. Worked against a local test fixture. A live
   query on a real machine got DuckDuckGo's generic error page with zero
   results -- no crash, just silently rejected.
2. **Second version**: swapped Playwright for plain `requests` +
   BeautifulSoup against the same HTML endpoint, based on current external
   documentation suggesting a full browser was the problem (DuckDuckGo's
   bot detection includes TLS fingerprinting a headless browser can't
   easily disguise). A live query this time returned **HTTP 202** with a
   generic DuckDuckGo homepage -- still rejected, just differently.
3. **Final version, current**: further research turned up the real
   answer -- `html.duckduckgo.com` is DuckDuckGo's **unofficial** results
   page, explicitly against their terms, and they **actively resist**
   automated access to it (their own documented behavior: "expect 202,
   403, and similar errors"). Continuing to reverse-engineer around that
   felt like the wrong thing to keep doing, not just a hard bug to fix.
   This agent now uses DuckDuckGo's **one real, sanctioned, documented
   API** instead: the Instant Answer API (`api.duckduckgo.com`,
   `format=json`) -- free, no key, actually meant for this.

**The real, honest tradeoff of the final approach**: the Instant Answer
API is NOT a ranked web-search results page. It returns curated content
(topic abstracts, definitions, disambiguation, related topics) for
well-known entities and concepts. Many completely reasonable
queries -- general questions, current events, long-tail topics -- will
come back with **nothing at all**. That's a real, inherent coverage gap,
not a bug, and the agent says so plainly when it happens (with a link to
run the same query directly on duckduckgo.com) rather than pretending to
have searched the whole web. This is the honest cost of using a free,
no-key, ToS-respecting API instead of a paid search API or continuing to
fight a service's bot detection.

**Honest limitation on testing, unchanged through all three versions**:
this sandbox's network is blocked from reaching any DuckDuckGo domain at
all, confirmed directly against both `html.duckduckgo.com` and
`api.duckduckgo.com` (identical "Host not in allowlist" result for each).
So the live API call itself still needs verification on a machine with
real internet access. What WAS verified: real parsing logic against
DuckDuckGo's actual documented JSON schema (`AbstractText`,
`AbstractSource`, `AbstractURL`, `Definition`, `Answer`, `RelatedTopics`
with `Text`/`FirstURL`) using a fake response object standing in for the
network call, real error handling (connection failures, invalid JSON),
and a real end-to-end CLI run confirming Planner -> ResearchAgent routing
works correctly.

## Run the tests


```bash
pip install pytest httpx
python -m pytest tests/ -v
```

371 tests, all passing: episodic memory, semantic recall, confirmation
gating, LLM graceful degradation, the web API's request/response contract,
the desktop app's server-readiness logic, the voice assistant's
conversation/confirmation logic, wake-word model loading, audio
silence-detection decision logic, sentence splitting, Whisper hallucination
filtering, real file operations, path-safety enforcement, real git
subprocess calls, real Playwright browser automation, and the new
WebSocket voice-event broadcast mechanism (real connect + real message
delivery, tested via FastAPI's TestClient).

## What's actually real here (updated)

- **Agent protocol, Orchestrator, Memory engine, Audit log**: as before —
  see below.
- **Coding agent** (`agents/coding.py`) and **General agent**
  (`agents/general.py`) are now backed by a **real local LLM via Ollama**
  (`llm/client.py`) — free, no API key, runs entirely on your machine. If
  Ollama isn't running, they degrade to a clear, honest message telling you
  how to start it (`ollama serve` / `ollama pull llama3.2`) rather than
  crashing or fabricating a response. Model and host are configurable via
  `SARVOS_OLLAMA_MODEL` / `SARVOS_OLLAMA_HOST` environment variables.
- Everything below from the original Phase 1a build is unchanged.

- **Agent protocol** (`core/schemas.py`): `Task` / `AgentResult` as
  Pydantic models — a real, typed contract every agent speaks.
- **Orchestrator** (`core/orchestrator.py`): a real task queue with
  recursive dispatch, and — this is the important part — **destructive-risk
  confirmation is enforced centrally**, not left to each agent to remember.
  An early version of this build had that gate living inside `CodingAgent`
  only, which meant "delete everything" (no coding keyword) skipped
  confirmation entirely. Caught by testing, fixed by moving the check to
  the one place every task passes through regardless of which agent
  handles it.
- **Memory engine** (`memory/engine.py`, `memory/store.py`): SQLite-backed
  episodic memory (real), semantic memory via TF-IDF with basic stemming
  (real, but see limitation below), procedural memory storage (real but
  unused by any agent yet), working memory (real, in-process scratch space).
- **Audit log**: every dispatch and every confirmation decision is
  persisted, append-only.

## What's explicitly stubbed (and why)

- **Planner** uses keyword/heuristic routing, not an LLM-based planner.
  This was deliberate: proving the plumbing (protocol, orchestrator,
  confirmation gating) works end-to-end *before* adding LLM cost and
  non-determinism on top of it. Swapping in an LLM-driven planner means
  replacing `PlannerAgent._decompose`; the Task/AgentResult contract
  doesn't change. (Coding and General agents are no longer stubbed — see
  above.)

## Real cross-platform bug: conversation order could reverse on Windows

Found from a real test failure that never once reproduced in the Linux
sandbox this project was built in: `recent_history()` ordered turns by
their `timestamp` string, but turns created in a tight loop with no delay
(exactly what happened in the test) could get **identical timestamps on
Windows** — its clock resolution is coarser than Linux's. SQLite's
tie-breaking for equal values isn't guaranteed to match insertion order,
so history could come back reversed.

Fixed by ordering by SQLite's implicit `rowid` instead (strictly
increases with insertion order, completely immune to clock resolution) —
applied to both `turns` (episodic memory) and `memory_records` (semantic
memory), the latter fixed proactively once the pattern was understood,
before it had actually caused a visible failure. Verified with a test
that *forces* identical timestamps deterministically (rather than
depending on real clock timing to happen to trigger the bug) — confirmed
this test genuinely catches the issue by temporarily reverting the fix
and watching it fail with the exact reversed-order symptom from the real
report.

## Known limitation: semantic search is lexical, not semantic

The spec calls for "vector embeddings." This build uses TF-IDF with light
stemming instead of a transformer embedding model, to avoid a multi-GB
dependency for a foundation build. Concretely: `test_tfidf_backend_is_lexical_not_semantic`
documents that a query like "what theme do I like" will **not** find a
memory saying "I prefer dark mode" — there's no shared vocabulary, and
TF-IDF doesn't understand that "theme" and "mode" are related concepts.
Stemming fixes morphological gaps (prefer/preferences) but not synonym
gaps. Swapping in `sentence-transformers` or an API embedding model is a
contained change to `SemanticIndex` in `memory/engine.py` — nothing above
that layer needs to change.

## How this maps to the original spec's phases

| Spec phase | Status here |
|---|---|
| Phase 1 — Foundation | Text chat + memory + orchestration: **done**. Voice: not started (deliberately split out — see below). |
| Phase 2 — Intelligence | Planning engine exists but is heuristic, not LLM-driven. Multi-agent collaboration protocol exists and is tested. Knowledge graph, screen understanding: not started. |
| Phase 3 — Automation | Not started. Confirmation-gating infrastructure it'll depend on is already in place. |
| Phase 4 / 5 | Not started. |

**One scope change from the original spec worth flagging explicitly:** the
spec bundled voice into Phase 1. This build splits it into 1a (this) and a
future 1b, because voice (wake word, streaming STT, TTS, interruption
handling) is a substantial project on its own, and coupling your hardest
UX problem to your foundation risks a rough first impression of the whole
system. Recommend keeping that split.

## Web UI design notes

The UI has been through two designs: an initial graphite/amber console
look, then a full redesign to the current JARVIS-style voice orb + chat
panel (70/30 split), per explicit spec. Current design:

- **Palette**: near-black background (`#0a0a0f`), cyan-to-blue glowing orb
  gradient — a deliberate, explicitly-requested aesthetic reference (JARVIS),
  not a generic default.
- **Orb states**: idle (slow ~4s breathing), listening (ripple + REAL
  microphone-amplitude-driven glow via Web Audio API's AnalyserNode —
  genuinely reactive, not simulated), thinking (tighter purple-tinted
  pulse while waiting on a response), speaking (pulse driven by
  `SpeechSynthesisUtterance`'s `onboundary` word-timing events — see
  honest limitation below).
- **Signature element carried over**: the System Audit Trail, now a
  slide-out drawer (toggle button in the header) instead of a persistent
  rail, to preserve the exact 70/30 orb/chat split the redesign spec asked
  for without losing that observability feature.

**Honest limitation, stated in the code too**: browsers do not expose
`SpeechSynthesis`'s audio output to `AnalyserNode` — there's no standard
way to get a real waveform reading from `window.speechSynthesis`. The
speaking-state pulse is therefore timing-driven (real per-word timing,
not real amplitude), both for typed-message TTS (browser SpeechSynthesis)
and reflected via WebSocket for the voice pipeline's responses. The
*listening* state's reactivity is fully real, by contrast — real
microphone amplitude, analyzed live.

## Wake-word → orb UI integration (WebSocket)

The standalone voice pipeline (`python -m voice.assistant`, wake word +
STT + TTS) now also drives this same web/desktop UI, not just a terminal.
Say "Hey Jarvis" while the desktop app or web UI is open, and the orb
visually reflects the pipeline's real state — listening, thinking,
speaking — with the transcript and response appearing in the chat panel
too, sharing the same orchestrator (and therefore the same memory/history)
as typed messages.

**How it works**: `api/server.py` starts the wake-word pipeline
(`VoiceAssistant`) on a background thread at server startup, sharing the
same `_orchestrator` used by `/api/chat`. The pipeline pushes state
through a thread-safe bridge (`call_soon_threadsafe` into an
`asyncio.Queue` — NOT a blocking `queue.Queue` wrapped in `asyncio.to_thread`,
which was tried first and caused the test suite to hang on shutdown,
since a blocked OS thread doesn't respond to asyncio-level cancellation)
to an async broadcaster, which fans events out to any browser connected
to `/ws/voice-events`.

**Gracefully degrades**: if voice dependencies aren't installed, or this
machine has no microphone, the server logs a message and keeps running
normally — text chat and everything else is unaffected. Verified directly:
in the sandbox this was built in (no microphone at all), the pipeline
attempts to start, fails cleanly with `Error querying device -1`, and the
server continues serving requests normally throughout.

**Per explicit choice, the browser's click-to-talk mic button (Web Speech
API) was removed entirely** in favor of wake-word-only voice input. Typed
messages still work exactly as before via the text input.

**Known scope limit**: confirmation state is tracked separately for typed
messages (`api/server.py`'s `_pending` dict) vs. voice (`VoiceAssistant`'s
own `_pending_task`) — a voice-triggered confirmation shows in the UI as
informational only (no clickable buttons; the prompt says to answer by
voice), since clicking Proceed/Cancel wouldn't resolve the voice
pipeline's separate pending state. Confirmations naturally resolve within
whichever modality triggered them, which covers the common case; unifying
the two pending-confirmation trackers is a reasonable future improvement,
not done here to keep this change scoped.

## Real mid-speech interruption

Earlier versions only checked for interruption *between* sentences. This
now checks continuously, in real time, while SARVOS is actually talking —
genuine barge-in, not just a gap-in-the-pauses approximation.

**Saying "stop" to actually stop**: interrupting a response and then
speaking a real follow-up question always worked, but there was no way
to just say "stop" or "never mind" and have SARVOS actually go quiet —
it would sit there waiting for a follow-up question that was never
coming. Now, if your very next utterance (after an interruption, or any
time there's no pending confirmation) is one of a recognized set of stop
phrases — "stop," "never mind," "cancel," "that's enough," "quiet," and
close variants — SARVOS goes idle immediately instead of treating it as
a real request. This is a whole-utterance match, not a substring check:
a genuine question like "how do I stop a car" is never mistaken for a
cancel command just because it contains the word "stop" (see
`tests/test_stop_command.py`'s negative-case tests). Note "stop" was
already a valid way to say "no" to an existing confirmation prompt —
that behavior is unchanged; the new check only applies when nothing is
currently pending confirmation.

**How**: `voice/tts.py`'s `speak_interruptible()` runs TTS on a background
thread while `voice/audio_io.py`'s `ContinuousMicMonitor` samples the
microphone in a loop on another thread; the moment your volume crosses
`SARVOS_BARGE_IN_RMS_THRESHOLD` (default `0.08`, deliberately higher than
normal speech detection's `0.02`), playback is cut instantly and SARVOS
starts listening to you.

**Why a separate, higher threshold**: there's no acoustic echo
cancellation in this build. Without a higher bar specifically for
interruption, SARVOS would frequently "interrupt itself" by hearing its
own voice through the speaker. This reduces that problem but doesn't
eliminate it — **a headset (mic physically separated from speaker
output) gives much more reliable results than tuning the threshold
alone.** If it's still too sensitive or not sensitive enough for your
setup:
```
set SARVOS_BARGE_IN_RMS_THRESHOLD=0.12
```

**A real crash found during live testing, and how it was fixed**: the
first version of interruption caused `RuntimeError: run loop already
started` after interrupting one utterance and then speaking the next —
pyttsx3's underlying Windows speech driver doesn't always finish tearing
down instantly after `engine.stop()`, and starting a second engine's
`runAndWait()` too soon collided with the first one's still-in-progress
shutdown. Worse, that crash **killed the entire background voice
pipeline thread** — "Hey Jarvis" stopped responding at all afterward,
which looked exactly like "stuck," not "crashed," from the outside, since
typed chat kept working fine (different code path). Two fixes, both
tested:
1. **Root cause**: `TextToSpeech` holds a lock across the *entire*
   duration of every speak call — so a new engine can never start while a
   previous one (even an interrupted one) hasn't finished. The original
   code used a 2-second timeout on the join, which could return before
   the engine had truly finished, letting the next call race ahead into
   the crash.
2. **Defense in depth**: both the per-turn conversation logic
   (`voice/assistant.py`) and the wake-word listen loop itself
   (`voice/wake_word.py`) catch exceptions locally — a failure in one
   turn logs an error and returns to wake-word-only listening, rather
   than propagating up and ending the entire pipeline.

**A SECOND real bug, found from the first fix itself, during further live
testing**: fixing #1 with an *unconditional* (infinite) `join()` traded
the crash for something worse — a permanent hang. If `engine.stop()`
doesn't actually unblock `runAndWait()` promptly (it doesn't always,
especially for longer responses), that join waits forever, freezing the
entire voice pipeline thread with **no crash, no traceback, just
silence** — interruption felt instant (stop() was called), then total
unresponsiveness afterward, exactly as reported. Fixed by bounding the
wait (`SARVOS_TTS_TEARDOWN_TIMEOUT_SECONDS`, default 3s): if the engine
hasn't finished tearing down by then, log a warning and move on anyway,
accepting a small residual chance the original crash recurs — which now
degrades gracefully via the defense-in-depth handling above instead of
freezing everything again. Verified with a test using a deliberately
"stubborn" fake engine whose `stop()` does nothing: `speak_interruptible`
now returns in ~0.3s instead of hanging for its simulated 10-second
run time.

## More natural, less robotic responses

The system prompts (`agents/general.py`) now explicitly push against
corporate-assistant phrasing — no more "I'd be happy to assist you with
that," "Certainly!," or "As an AI, I don't have..." Instead: contractions,
directness, brevity, and permission to have a bit of personality. Applies
to both typed and spoken responses. This is prompt-level guidance to a
small local model (llama3.2) — expect it to help noticeably, not to be
perfect every time.

## Auto-start at login (Windows)

So SARVOS is running and listening for "Hey Jarvis" whenever you sit down
at the laptop, without manually launching it first:

1. Confirm `start_sarvos.bat` (in the project root) works by double-clicking
   it — it should launch SARVOS with no visible console window.
2. Press `Win + R`, type `shell:startup`, press Enter — this opens your
   Windows Startup folder.
3. Right-click `start_sarvos.bat` → **Create shortcut**, then drag that
   shortcut into the Startup folder from step 2.
4. Log out and back in (or restart) to confirm it launches automatically.

**What to expect**: the app window starts **minimized** — out of your way,
but still running and listening — and automatically restores and comes to
the front the moment "Hey Jarvis" triggers, so you don't need to have
already had it open or focused.

**Optional — give the Startup shortcut the SARVOS icon too**: the app
window/taskbar icon (a cyan-to-blue orb, matching the UI) is wired up
automatically via `static/sarvos_icon.ico`. Windows shortcuts (`.lnk`
files) pick their own icon separately, so to make the Startup-folder
shortcut match:
1. Right-click the shortcut you created in step 3 above → **Properties**
2. Click **Change Icon...**
3. Click **Browse...**, navigate to `Desktop\sarvos\static\sarvos_icon.ico`,
   select it, click **OK** twice.

## Project layout

```
core/
  schemas.py        Task, AgentResult, ConversationTurn, MemoryRecord
  orchestrator.py    Task queue, routing, confirmation gating, audit logging
  factory.py         Shared create_orchestrator() -- used by CLI, web, voice
                      (added to stop three separate copies of the same
                      wiring code from drifting out of sync)
agents/
  base.py            BaseAgent interface
  planner.py         Executive Planner (heuristic routing)
  coding.py          Coding agent (real LLM via Ollama, graceful fallback)
  general.py         General conversational agent (real LLM via Ollama)
  memory_agent.py    Memory agent (remember/recall/forget)
  automation.py      REAL file ops + git, sandboxed (first agent with
                      actual side effects, not just generated text)
  automation_intent.py  Shared intent classification (Planner + agent
                          agree on what an instruction means and its risk)
  automation_config.py  Workspace sandbox root, size/timeout limits
  browser.py         Real Playwright browser automation (read-only)
  browser_intent.py  Browser instruction classification + scheme safety
  browser_config.py  Screenshot sandbox, timeouts, headless default
  research.py        Real Playwright web search (DuckDuckGo HTML endpoint)
  research_intent.py Research instruction classification
  research_config.py Search URL template, result limits, timeouts
memory/
  store.py           SQLite persistence (episodic, semantic, procedural, audit)
  engine.py          MemoryEngine facade + TF-IDF SemanticIndex
llm/
  config.py          Environment-driven config, free/local defaults
  client.py          LLMClient interface + OllamaClient implementation
api/
  server.py          FastAPI wrapper around the orchestrator (web UI backend)
static/
  index.html         Web UI: chat + live audit-trail rail
voice/
  config.py          Voice settings (wake word, VAD thresholds, etc.)
  audio_io.py         Microphone recording + silence detection
  wake_word.py       openWakeWord detector
  stt.py             faster-whisper speech-to-text
  tts.py             pyttsx3 text-to-speech, graceful fallback
  assistant.py       VoiceAssistant -- conversation logic (testable) +
                      real audio loop (not testable without hardware)
tests/
  test_memory.py
  test_orchestrator.py
  test_agents.py     Memory-agent parsing regression tests
  test_llm_client.py Ollama-unavailable graceful degradation tests
  test_api.py        FastAPI endpoint + confirmation-flow tests
  test_desktop.py    Desktop server-readiness logic tests
  test_voice_assistant.py  Voice conversation/confirmation logic tests
  test_wake_word.py  Real (non-mocked) openWakeWord model loading tests
main.py              CLI entry point (still works, independent of the web UI)
desktop.py           Desktop app entry point (pywebview native window)
```

## Suggested next step

Given the roster in the spec (Research, Browser, DevOps, Salesforce
Specialist, ...), the next highest-leverage addition is probably **one more
real agent wired to an actual capability** — e.g. a Research agent that
does a real web search — to prove the protocol generalizes beyond the two
stub agents here, before investing in voice or the UI layer.
