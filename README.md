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

**Not yet built**: browser automation, IDE integration, and a general
workflow engine — each a substantial separate project (browser automation
alone needs Playwright plus real page-state handling). This automation
agent is the foundation those would eventually call into, not a
replacement for building them.

## Run the tests

```bash
pip install pytest httpx
python -m pytest tests/ -v
```

94 tests, all passing: episodic memory, semantic recall, confirmation
gating, LLM graceful degradation, the web API's request/response contract,
the desktop app's server-readiness logic, the voice assistant's
conversation/confirmation logic, wake-word model loading, audio
silence-detection decision logic, sentence splitting, Whisper hallucination
filtering, and — new — real file operations, path-safety enforcement, and
real git subprocess calls for the Automation agent, including an
end-to-end test proving the confirmation gate actually blocks/allows a
REAL filesystem effect, not just a simulated one.

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

Deliberately not a generic chat-bubble template. Design tokens:

- **Palette**: deep graphite background (`#14171c`), warm amber accent
  (`#d9a648`) — chosen over the more common near-black+neon-green AI
  aesthetic to fit "personal operating system" rather than "chatbot demo."
  Destructive/risk states use a separate coral-red (`#d9614f`), never the
  primary accent, so danger reads as an exception rather than the theme.
- **Type**: IBM Plex Mono for system/audit data and the wordmark (technical,
  "operating system" register), Inter for conversational text (readable,
  human register). The split itself signals which parts of the UI are
  "system" vs. "conversation."
- **Signature element**: the right-hand System/Audit Trail rail — a live,
  terminal-styled readout of the same audit log the orchestrator already
  writes to SQLite. This isn't decorative: it's the spec's "every action
  should be observable" principle made visible in real time, color-coded by
  risk level, rather than something you'd have to go query separately.

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
