"""
BrowserAgent -- real, read-only web browsing via Playwright (headless
Chromium). Same safety philosophy as agents/automation.py: deterministic
instruction parsing (agents/browser_intent.py), not an LLM freely deciding
what to browse to or what to click.

Scope, deliberately: open a page and extract its title/text, or take a
screenshot. NOT included in this version: form filling, submission,
login, downloads, or multi-step flows -- those have real side effects on
external sites and deserve their own separately-scoped, separately-tested
work, the same way file writes/deletes got dedicated care rather than
being bundled into the first pass.

A fresh browser is launched per call rather than kept running persistently
-- simpler and safer for a first version (no session/cookie state leaking
between unrelated requests), at the cost of being slower than a
long-lived browser would be. Revisit if that latency becomes a real
problem once this is used more.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents import browser_config
from agents.base import BaseAgent
from agents.browser_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task


def _safe_filename_from_url(url: str) -> str:
    """Turns a URL into a filesystem-safe filename for screenshots."""
    stripped = re.sub(r"^https?://", "", url)
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", stripped)
    return safe[:100] or "page"


class BrowserAgent(BaseAgent):
    name = AgentName.BROWSER

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)

        if intent.operation == Operation.OPEN_URL:
            return self._open_url(task, intent.url)
        if intent.operation == Operation.SCREENSHOT:
            return self._screenshot(task, intent.url)

        return AgentResult(
            task_id=task.task_id, agent=self.name, success=False,
            output=(
                f"I couldn't work out a browsing action from: "
                f"'{task.instruction}'. Try 'open website example.com' or "
                f"'take a screenshot of example.com'."
            ),
        )

    def _launch_page(self):
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=browser_config.HEADLESS)
        page = browser.new_page()
        return playwright, browser, page

    def _open_url(self, task: Task, url: str) -> AgentResult:
        playwright = browser = None
        try:
            playwright, browser, page = self._launch_page()
            page.goto(url, timeout=browser_config.PAGE_LOAD_TIMEOUT_MS)
            title = page.title()
            text = page.inner_text("body")
            truncated = text[: browser_config.MAX_TEXT_LENGTH]
            if len(text) > browser_config.MAX_TEXT_LENGTH:
                truncated += "... (truncated)"
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"'{title}' ({url}):\n{truncated}",
                data={"url": url, "title": title},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't open '{url}': {e}",
                error="browser_navigation_failed",
            )
        finally:
            if browser:
                browser.close()
            if playwright:
                playwright.stop()

    def _screenshot(self, task: Task, url: str) -> AgentResult:
        playwright = browser = None
        try:
            playwright, browser, page = self._launch_page()
            page.goto(url, timeout=browser_config.PAGE_LOAD_TIMEOUT_MS)

            screenshot_dir = Path(browser_config.SCREENSHOT_DIR)
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            filename = _safe_filename_from_url(url) + ".png"
            screenshot_path = screenshot_dir / filename
            page.screenshot(path=str(screenshot_path))

            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"Screenshot of {url} saved to {screenshot_path}.",
                data={"url": url, "screenshot_path": str(screenshot_path)},
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=f"Couldn't screenshot '{url}': {e}",
                error="browser_navigation_failed",
            )
        finally:
            if browser:
                browser.close()
            if playwright:
                playwright.stop()
