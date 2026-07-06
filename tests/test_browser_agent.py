import http.server
import tempfile
import threading
from pathlib import Path

import pytest

from agents.browser import BrowserAgent
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str) -> Task:
    return Task(parent_request_id="r1", agent=AgentName.BROWSER, instruction=instruction)


@pytest.fixture(scope="module")
def local_server():
    """A real local HTTP server serving a real test page -- lets these
    tests exercise REAL Playwright navigation/extraction without needing
    external network access (not available in this sandbox anyway, and
    better for reliable/repeatable tests regardless)."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "index.html").write_text(
            "<html><head><title>Test Page Title</title></head>"
            "<body><h1>Hello SARVOS</h1><p>This is a real test page.</p></body></html>"
        )

        handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(
            *args, directory=tmp, **kwargs
        )
        server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}/index.html"
        server.shutdown()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agents.browser_config.SCREENSHOT_DIR", str(tmp_path / "screenshots")
    )
    memory = MemoryEngine(store=Store(tmp_path / "test.db"))
    yield BrowserAgent(memory)


def test_open_real_local_page_extracts_title_and_text(agent, local_server):
    result = agent.handle(_task(f"open website {local_server}"))
    assert result.success
    assert "Test Page Title" in result.output
    assert "Hello SARVOS" in result.output
    assert "real test page" in result.output


def test_open_nonexistent_server_fails_gracefully(agent):
    result = agent.handle(_task("open website http://127.0.0.1:1/nope"))
    assert not result.success
    assert result.error == "browser_navigation_failed"


def test_screenshot_of_real_page_creates_real_file(agent, local_server, tmp_path):
    result = agent.handle(_task(f"take a screenshot of {local_server}"))
    assert result.success
    screenshot_path = Path(result.data["screenshot_path"])
    assert screenshot_path.exists()
    assert screenshot_path.stat().st_size > 0  # a real PNG was actually written


def test_unrecognized_instruction_gives_helpful_message(agent):
    result = agent.handle(_task("do something browser-ish but vague"))
    assert not result.success
    assert "open website" in result.output.lower()


def test_blocked_scheme_never_reaches_playwright(agent):
    """The intent classifier should refuse this before BrowserAgent ever
    calls Playwright -- verifying the safety boundary holds end-to-end
    through the agent, not just in the classifier's own unit tests."""
    result = agent.handle(_task("open website file:///etc/passwd"))
    assert not result.success
    assert "open website" in result.output.lower()  # the "I don't understand" message
