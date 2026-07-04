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
python main.py
```

Try:
```
remember that I prefer dark mode
what do you know about my preferences
debug this function
delete all my files        <- triggers a confirmation prompt (y/n)
log                          <- shows the audit trail
```

## Run the tests

```bash
pip install pytest
python -m pytest tests/ -v
```

21 tests, all passing, covering episodic memory, semantic recall, the
confirmation-gating logic (since that's the part most likely to silently
regress), and the LLM client's graceful-degradation path (Ollama not
running should never crash the CLI).

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

## Project layout

```
core/
  schemas.py        Task, AgentResult, ConversationTurn, MemoryRecord
  orchestrator.py    Task queue, routing, confirmation gating, audit logging
agents/
  base.py            BaseAgent interface
  planner.py         Executive Planner (heuristic routing)
  coding.py          Coding agent (real LLM via Ollama, graceful fallback)
  general.py         General conversational agent (real LLM via Ollama)
  memory_agent.py    Memory agent (remember/recall/forget)
memory/
  store.py           SQLite persistence (episodic, semantic, procedural, audit)
  engine.py          MemoryEngine facade + TF-IDF SemanticIndex
llm/
  config.py          Environment-driven config, free/local defaults
  client.py          LLMClient interface + OllamaClient implementation
tests/
  test_memory.py
  test_orchestrator.py
  test_agents.py     Memory-agent parsing regression tests
  test_llm_client.py Ollama-unavailable graceful degradation tests
main.py              CLI entry point
```

## Suggested next step

Given the roster in the spec (Research, Browser, DevOps, Salesforce
Specialist, ...), the next highest-leverage addition is probably **one more
real agent wired to an actual capability** — e.g. a Research agent that
does a real web search — to prove the protocol generalizes beyond the two
stub agents here, before investing in voice or the UI layer.
