"""
General Agent — fallback conversational agent for anything not routed to a
specialist (Coding, Memory). Backed by the same local Ollama LLM as the
Coding agent (free, no API key).

Includes recent conversation history as context so replies aren't
single-turn-blind — without this, every message would be answered as if it
were the first thing you'd ever said to SARVOS.
"""

from __future__ import annotations

import re

from core.schemas import AgentName, AgentResult, Task
from agents.base import BaseAgent
from llm.client import LLMUnavailable, get_llm_client

SYSTEM_PROMPT = (
    "You are SARVOS, a calm, confident, helpful personal AI assistant. Be "
    "concise by default, detailed when asked. Never pretend to know "
    "something you don't; acknowledge uncertainty plainly. Talk like a "
    "person, not a corporate assistant -- skip phrases like 'I'd be happy "
    "to help', 'Certainly!', or 'As an AI'; just answer directly, using "
    "contractions, like a knowledgeable friend would rather than a "
    "customer service script."
    "\n\nCRITICAL, non-negotiable: you have NO access to the user's files, "
    "filesystem, terminal, or any system state. You cannot read files, run "
    "commands, or apply changes -- other agents do that, not you. If asked "
    "about the contents of a file, whether a change was applied, or what a "
    "command printed, say plainly that you can't see it, and name the real "
    "command they should run. NEVER invent file contents. NEVER show a diff "
    "or a 'before/after' code block describing the user's actual files. "
    "NEVER say a change has been applied. This has really happened in this "
    "project: a user was told their file 'has been updated', complete with a "
    "fabricated code block -- nothing had been written at all. Confident "
    "prose about changes that never happened is far worse than admitting "
    "you cannot see anything."
)

SPOKEN_SYSTEM_PROMPT = (
    "You are SARVOS. You're talking out loud to someone, not writing them "
    "a message -- talk like an actual person would, not like a corporate "
    "assistant. Concretely:\n"
    "- Never say things like 'I'd be happy to assist you with that', "
    "'Certainly!', 'I'm just a language model', 'As an AI', or any other "
    "stock customer-service phrase. Just answer, the way a knowledgeable "
    "friend would.\n"
    "- Use contractions (I'm, you're, don't, it's) -- that's how people "
    "actually talk.\n"
    "- Skip the hedging and disclaimers unless they're genuinely useful. "
    "If you don't know something, just say 'I don't know' or 'not sure', "
    "not a paragraph explaining your limitations as a language model.\n"
    "- Keep it short and conversational -- one or two sentences for most "
    "things, like a real back-and-forth, not a monologue.\n"
    "- No numbered lists, bullet points, or markdown -- this is speech, "
    "not a document. Weave options into a sentence instead of listing them.\n"
    "- It's fine to have a bit of personality and warmth. You don't need "
    "to sound neutral or corporate."
    "\n- CRITICAL: you have NO access to the user's files, filesystem, or "
    "terminal. You can't read files, run commands, or apply changes. If "
    "asked what's in a file or whether a change was applied, just say you "
    "can't see it. Never invent file contents and never claim a change was "
    "made -- confidently describing changes that never happened is far "
    "worse than saying you don't know."
)

HISTORY_TURNS = 6  # how much recent conversation to include as context

# Lines that mark a block as a *diff* about real files, rather than an
# ordinary code example. A general chat agent with no filesystem access
# can never legitimately produce one of these.
_DIFF_MARKERS = ("--- a/", "+++ b/", "@@ ")

_STRIPPED_NOTICE = (
    "[SARVOS removed a diff the assistant tried to show here. It has no "
    "access to your files and cannot read them, so any diff it produces is "
    "reconstructed from conversation, not from disk -- and would look "
    "convincing while being wrong. To see the real file, run "
    "`type <file>` (Windows) or `cat <file>` at your shell. To see a real, "
    "verified diff of a proposed change, use: propose a fix for <file>]"
)


_ACTION_CLAIM_NOTICE = (
    "\n\n[SARVOS note: the assistant claimed to have performed an action. It "
    "cannot -- it has no shell, no filesystem, and no ability to run "
    "anything. Any such claim is invented. Real work is done by the other "
    "agents, and you can see it happen: 'run the tests', 'read <file>', "
    "'propose a fix for <file>'.]"
)

# First-person, past-tense claims of having DONE something. Deliberately
# narrow: "you could run the tests" and "try running it" are fine advice.
# "I ran the tests" is a lie, because this agent cannot run anything.
_ACTION_CLAIM_RE = re.compile(
    r"\bI(?:'ve| have)?\s+(?:just\s+|already\s+)?"
    r"(?:ran|run|executed|checked|opened|read|looked\s+at|inspected|"
    r"searched|applied|wrote|written|created|deleted|modified|updated)\b"
    r"|\bI(?:'m| am)\s+(?:now\s+)?(?:running|executing|applying|reading)\b",
    re.I,
)


def flag_fabricated_actions(text: str) -> str:
    """Append a correction when the general agent claims to have acted.

    Found in live use: typing 'python main.py' at the SARVOS prompt reached
    this agent, which answered "I've run the command, but I still can't find
    any information about a file named resume.pdf". It ran nothing. There is
    no resume.pdf. Both halves were invented.

    The diff filter catches fabricated *diffs*; this catches fabricated
    *actions*, which are the same falsehood in prose form. Enforced in code
    for the same reason: the system prompt already forbade this, and the
    model did it anyway. Prompts shape behavior; only code constrains it.

    Kept deliberately narrow. The response is annotated, not suppressed --
    the model may still have said something useful, and silently deleting
    text would trade one opacity for another.
    """
    if _ACTION_CLAIM_RE.search(text):
        return text.rstrip() + _ACTION_CLAIM_NOTICE
    return text


def strip_fabricated_diffs(text: str) -> str:
    """Remove diff blocks from general-agent output, in code.

    A system prompt is a request, not a constraint. Confirmed directly: told
    categorically to never show a before/after block describing the user's
    real files, llama3.2 said "You can't see the contents of calc.py" and
    then displayed a diff of that exact file anyway -- reconstructed from
    the conversation, with the indentation wrong. It was plausible and it
    was false.

    So this is enforced where it cannot be talked out of: any fenced block
    or run of lines carrying diff markers is replaced with an explicit
    notice. Ordinary code examples are left alone -- the agent may well be
    asked a legitimate coding question; it just may never claim to show the
    contents of the user's actual files.
    """
    if not any(marker in text for marker in _DIFF_MARKERS):
        return text

    out, buf, in_fence, fence_has_diff = [], [], False, False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence, fence_has_diff, buf = True, False, [line]
            else:
                buf.append(line)
                out.append(_STRIPPED_NOTICE + "\n" if fence_has_diff else "".join(buf))
                in_fence, fence_has_diff, buf = False, False, []
            continue

        if in_fence:
            buf.append(line)
            if any(stripped.startswith(m) for m in _DIFF_MARKERS):
                fence_has_diff = True
        elif any(stripped.startswith(m) for m in _DIFF_MARKERS):
            # A bare, unfenced diff line -- drop it, noting once.
            if not out or not out[-1].startswith(_STRIPPED_NOTICE):
                out.append(_STRIPPED_NOTICE + "\n")
        else:
            out.append(line)

    if in_fence:  # unterminated fence
        out.append(_STRIPPED_NOTICE + "\n" if fence_has_diff else "".join(buf))

    return "".join(out).strip()


class GeneralAgent(BaseAgent):
    name = AgentName.GENERAL

    def handle(self, task: Task) -> AgentResult:
        prompt = self._build_prompt(task.instruction)
        system_prompt = SPOKEN_SYSTEM_PROMPT if task.context.get("spoken") else SYSTEM_PROMPT
        try:
            client = get_llm_client()
            response = client.generate(prompt, system=system_prompt)
            response = flag_fabricated_actions(strip_fabricated_diffs(response))
        except LLMUnavailable as e:
            response = (
                f"[general-agent: local LLM unavailable] {e}\n\n"
                f"Once Ollama is running, I'll actually respond to: "
                f"'{task.instruction}'"
            )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True, output=response,
        )

    def _build_prompt(self, instruction: str) -> str:
        history = self.memory.recent_history(limit=HISTORY_TURNS)
        if not history:
            return instruction
        transcript = "\n".join(f"{t.role}: {t.content}" for t in history)
        return f"Recent conversation:\n{transcript}\n\nuser: {instruction}"
