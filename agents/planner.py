"""
Executive Planner agent.

Implements the spec's lifecycle: Understand -> Clarify -> Plan -> Estimate
-> Identify tools -> Execute -> Verify -> Summarize -> Store.

For Phase 1a this is a rule-based decomposer (keyword/heuristic routing),
NOT an LLM-driven planner. That's an intentional, explicit scope cut: wiring
this to an actual LLM call is a follow-up (drop-in replacement of
`_decompose`), and shipping the honest rule-based version now means the
orchestrator/agent-protocol plumbing gets tested end-to-end before adding
LLM cost and latency on top of it.
"""

from __future__ import annotations

from core.schemas import AgentName, AgentResult, RiskLevel, Task
from agents.base import BaseAgent
from agents.automation_intent import classify as classify_automation, Operation as AutomationOp
from agents.browser_intent import classify as classify_browser, Operation as BrowserOp
from agents.research_intent import classify as classify_research, Operation as ResearchOp

DESTRUCTIVE_KEYWORDS = ("delete", "remove", "drop", "wipe", "format", "rm ")
CODE_KEYWORDS = ("code", "function", "bug", "debug", "refactor", "script", "class ")
MEMORY_KEYWORDS = (
    "remember", "recall", "what did i", "forget", "my preference",
    # Broadened after a real gap was found: "what do you know about X" is
    # a completely natural way to ask about something you told SARVOS to
    # remember, but none of the keywords above matched it -- it silently
    # skipped memory recall entirely and went straight to general chat,
    # which has no access to stored facts at all. A test relying on this
    # exact phrasing only passed before by accident (the LLM-unavailable
    # fallback happened to echo the input text verbatim, masking that the
    # routing gap existed) -- it failed for real once Ollama was actually
    # running and gave a genuine answer that didn't happen to repeat the
    # keyword.
    "what do you know", "do you know", "have i told you", "have i mentioned",
)


class PlannerAgent(BaseAgent):
    name = AgentName.PLANNER

    def handle(self, task: Task) -> AgentResult:
        subtasks = self._decompose(task)
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            success=True,
            output=f"Decomposed into {len(subtasks)} subtask(s).",
            new_tasks=subtasks,
        )

    def _decompose(self, task: Task) -> list[Task]:
        # Automation and Browser each get first refusal via their own
        # precise classifiers, before falling through to the generic
        # keyword matching below. Automation checked first since "read
        # file X" and "open website X" are unambiguous and don't overlap.
        automation_intent = classify_automation(task.instruction)
        if automation_intent.operation != AutomationOp.UNKNOWN:
            return [
                Task(
                    parent_request_id=task.parent_request_id,
                    agent=AgentName.AUTOMATION,
                    instruction=task.instruction,
                    context=task.context,
                    risk=automation_intent.risk,
                )
            ]

        browser_intent = classify_browser(task.instruction)
        if browser_intent.operation != BrowserOp.UNKNOWN:
            return [
                Task(
                    parent_request_id=task.parent_request_id,
                    agent=AgentName.BROWSER,
                    instruction=task.instruction,
                    context=task.context,
                    risk=browser_intent.risk,
                )
            ]

        research_intent = classify_research(task.instruction)
        if research_intent.operation != ResearchOp.UNKNOWN:
            return [
                Task(
                    parent_request_id=task.parent_request_id,
                    agent=AgentName.RESEARCH,
                    instruction=task.instruction,
                    context=task.context,
                    risk=research_intent.risk,
                )
            ]

        text = task.instruction.lower()

        # Destructive-intent detection runs FIRST and independently of which
        # agent ends up handling the task. This matters: "delete everything"
        # has no coding keyword in it, but it's still destructive. Risk is a
        # property of the instruction's *effect*, not of which specialty
        # handles it — so it can't be scoped to just one category's branch.
        risk = (
            RiskLevel.DESTRUCTIVE
            if any(k in text for k in DESTRUCTIVE_KEYWORDS)
            else RiskLevel.SAFE
        )

        if any(k in text for k in MEMORY_KEYWORDS):
            agent = AgentName.MEMORY
        elif any(k in text for k in CODE_KEYWORDS):
            agent = AgentName.CODING
        else:
            agent = AgentName.GENERAL

        return [
            Task(
                parent_request_id=task.parent_request_id,
                agent=agent,
                instruction=task.instruction,
                context=task.context,
                risk=risk,
            )
        ]
