"""
ResearchAgent -- real web search via DuckDuckGo's OFFICIAL Instant Answer
API (agents/research_config.py). Free, no API key, no browser needed.

REAL PIVOT, from live testing then confirmed via research, not guessed:
earlier versions of this agent tried Playwright, then plain HTTP requests,
against html.duckduckgo.com -- DuckDuckGo's UNOFFICIAL, no-JS results
page. Both got actively blocked (a real live request returned HTTP 202
with a generic homepage instead of results), which matches documented
DuckDuckGo behavior: they explicitly resist automated access to that
endpoint, since it isn't meant for programmatic use. Continuing to work
around that felt like the wrong thing to keep doing. This version uses
DuckDuckGo's real, sanctioned API instead.

REAL TRADEOFF, stated plainly, not hidden: the Instant Answer API returns
curated content (topic abstracts, definitions, disambiguation, related
topics) for well-known entities/concepts. It is NOT a ranked web-search
results page -- many reasonable queries (general questions, current
events, long-tail topics) will come back with nothing at all. That's an
inherent, real coverage gap, not a bug. When nothing is found, this agent
says so plainly and suggests visiting DuckDuckGo directly, rather than
pretending to have searched the whole web.

This is the third real-capability agent (after Automation and Browser),
built to prove the agent protocol generalizes to a new capability -- and,
as it turned out through two real pivots, generalizes to genuinely
different implementation approaches too, each chosen for real reasons
found along the way rather than assumed up front.
"""

from __future__ import annotations

import requests

from agents import research_config
from agents.base import BaseAgent
from agents.research_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task


class ResearchAgent(BaseAgent):
    name = AgentName.RESEARCH

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        if intent.operation == Operation.SEARCH:
            return self._search(task, intent.query)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=False,
            output=(
                f"I couldn't work out a search query from: "
                f"'{task.instruction}'. Try 'research X', 'search for X', "
                f"or 'look up X'."
            ),
        )

    def _search(self, task: Task, query: str) -> AgentResult:
        try:
            response = requests.get(
                research_config.SEARCH_URL_TEMPLATE,
                params={
                    "q": query,
                    "format": "json",
                    "no_html": "1",
                    "skip_disambig": "1",
                    "t": research_config.APP_IDENTIFIER,
                },
                timeout=research_config.PAGE_LOAD_TIMEOUT_MS / 1000,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't search for '{query}': {e}",
                error="research_search_failed",
            )
        except ValueError as e:
            # response.json() failed -- the API returned something that
            # isn't valid JSON (an HTML error page, empty body, etc.).
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't parse search results for '{query}': {e}",
                error="research_parse_failed",
            )

        return self._format_result(task, query, data)

    def _format_result(self, task: Task, query: str, data: dict) -> AgentResult:
        parts = []

        abstract = (data.get("AbstractText") or "").strip()
        abstract_source = (data.get("AbstractSource") or "").strip()
        abstract_url = (data.get("AbstractURL") or "").strip()
        if abstract:
            snippet = abstract[: research_config.MAX_SNIPPET_LENGTH]
            source_note = f" (via {abstract_source})" if abstract_source else ""
            parts.append(f"{snippet}{source_note}")
            if abstract_url:
                parts.append(f"More: {abstract_url}")

        answer = (data.get("Answer") or "").strip()
        if answer:
            parts.append(f"Answer: {answer}")

        definition = (data.get("Definition") or "").strip()
        definition_url = (data.get("DefinitionURL") or "").strip()
        if definition:
            parts.append(f"Definition: {definition[:research_config.MAX_SNIPPET_LENGTH]}")
            if definition_url:
                parts.append(f"Source: {definition_url}")

        related_entries = []
        if not parts:
            # Only fall back to RelatedTopics if there's no direct
            # abstract/answer/definition -- those are more precise, and
            # RelatedTopics can be a fairly loose/broad list.
            for topic in (data.get("RelatedTopics") or [])[: research_config.MAX_RESULTS]:
                text = (topic.get("Text") or "").strip()
                url = (topic.get("FirstURL") or "").strip()
                if text:
                    related_entries.append((text, url))

        if not parts and not related_entries:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=(
                    f"I didn't find an instant answer for '{query}'. "
                    f"DuckDuckGo's free API only covers well-known topics "
                    f"and definitions, not full web search results -- try "
                    f"https://duckduckgo.com/?q={requests.utils.quote(query)} "
                    f"directly for a full results page."
                ),
                data={"query": query, "results": []},
            )

        if related_entries:
            lines = [f"{i}. {text} ({url})" if url else f"{i}. {text}"
                      for i, (text, url) in enumerate(related_entries, 1)]
            output = f"Related to '{query}':\n" + "\n".join(lines)
            results_data = [{"text": t, "url": u} for t, u in related_entries]
        else:
            output = f"'{query}':\n" + "\n".join(parts)
            results_data = [{"text": p} for p in parts]

        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=output, data={"query": query, "results": results_data},
        )
