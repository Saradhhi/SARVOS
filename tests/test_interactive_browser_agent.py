"""
Real end-to-end tests for InteractiveBrowserAgent against a local HTTP
server serving a real HTML form. Playwright runs headless here (the
existing browser-agent tests already prove that works in this sandbox),
so this genuinely exercises the full stateful flow -- open, type, click,
read, submit -- not a mock.
"""

import http.server
import tempfile
import threading
from pathlib import Path

import pytest

from agents.interactive_browser import InteractiveBrowserAgent
from core.factory import create_orchestrator
from core.orchestrator import PendingConfirmation
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str) -> Task:
    return Task(parent_request_id="r1", agent=AgentName.INTERACTIVE_BROWSER, instruction=instruction)


_FORM_HTML = """
<html><head><title>Test Login</title></head><body>
<h1>Test Login</h1>
<form id="loginform" method="get" action="/submitted.html">
  <input type="text" name="username" placeholder="Username" aria-label="Username" />
  <input type="password" name="password" placeholder="Password" aria-label="Password" />
  <button type="submit">Log In</button>
</form>
</body></html>
"""

_SUBMITTED_HTML = "<html><body><h1>Submitted Successfully</h1></body></html>"


_DISABLED_BUTTON_HTML = """
<html><head><title>Search Page</title></head><body>
<h1>Search Page</h1>
<form method="get" action="/submitted.html">
  <input type="text" name="q" placeholder="Search" aria-label="Search" id="q"
    onkeydown="if(event.key==='Enter'){this.form.submit();}" />
  <button type="submit" disabled style="display:none">Search</button>
</form>
</body></html>
"""
# Models real modern search sites (e.g. DuckDuckGo) faithfully: the submit
# button is deliberately disabled + hidden, and the form submits via a JS
# keydown handler on Enter -- NOT native form submission, which Chromium
# genuinely blocks when the only submit control is disabled (confirmed
# empirically). This is exactly the case that broke the original _submit:
# it found the disabled button and timed out clicking it, instead of
# falling through to Enter.


@pytest.fixture(scope="module")
def local_server():
    tmp = tempfile.mkdtemp()
    (Path(tmp) / "login.html").write_text(_FORM_HTML)
    (Path(tmp) / "submitted.html").write_text(_SUBMITTED_HTML)
    (Path(tmp) / "search.html").write_text(_DISABLED_BUTTON_HTML)

    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=tmp, **k)
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def agent(tmp_path):
    memory = MemoryEngine(store=Store(tmp_path / "test.db"))
    a = InteractiveBrowserAgent(memory)
    yield a
    a.session.close()  # always clean up the browser


def test_actions_before_open_report_no_session(agent):
    result = agent.handle(_task('type "hi" into the username field'))
    assert not result.success
    assert result.error == "no_session"


def test_open_starts_a_live_session(agent, local_server):
    result = agent.handle(_task(f"open a browser session at {local_server}/login.html"))
    assert result.success
    assert agent.session.is_open()
    assert "Test Login" in result.output


def test_full_flow_open_type_and_read(agent, local_server):
    agent.handle(_task(f"open a browser session at {local_server}/login.html"))

    typed = agent.handle(_task('type "myuser" into the username field'))
    assert typed.success
    # Must NOT echo the typed value back (could be a password).
    assert "myuser" not in typed.output

    # Verify the value actually landed in the real field.
    value = agent.session.page.input_value("input[name='username']")
    assert value == "myuser"


def test_type_into_missing_field_fails_clearly(agent, local_server):
    agent.handle(_task(f"open a browser session at {local_server}/login.html"))
    result = agent.handle(_task('type "x" into the nonexistent_field_xyz field'))
    assert not result.success
    assert result.error == "field_not_found"


def test_read_page_returns_real_content(agent, local_server):
    agent.handle(_task(f"open a browser session at {local_server}/login.html"))
    result = agent.handle(_task("read the page"))
    assert result.success
    assert "Test Login" in result.output


def test_close_session(agent, local_server):
    agent.handle(_task(f"open a browser session at {local_server}/login.html"))
    assert agent.session.is_open()
    result = agent.handle(_task("close the browser session"))
    assert result.success
    assert not agent.session.is_open()


def test_submit_is_gated_and_executes_real_form_after_approval(tmp_path, local_server):
    """The critical safety + functionality test: submit is DESTRUCTIVE,
    so it must be gated by the real orchestrator BEFORE the form actually
    submits -- and after approval, it must genuinely submit the real form
    and navigate to the result page. Proven end-to-end through the real
    orchestrator against a real local form."""
    orchestrator = create_orchestrator(str(tmp_path / "test.db"))

    # Open + type happen without gating (SAFE).
    orchestrator.handle_user_message(
        f"open a browser session at {local_server}/login.html", request_id="r1"
    )
    orchestrator.handle_user_message('type "myuser" into the username field', request_id="r1")

    agent = orchestrator.agents[AgentName.INTERACTIVE_BROWSER]

    # Submit must raise PendingConfirmation and NOT have navigated yet.
    try:
        orchestrator.handle_user_message("submit the form", request_id="r1")
        pytest.fail("Expected submit to be gated with PendingConfirmation")
    except PendingConfirmation as e:
        pending = e

    assert "login.html" in agent.session.page.url, (
        "Form submitted BEFORE confirmation -- must not happen."
    )

    results = orchestrator.resume_with_confirmation(pending.task, approved=True, request_id="r1")
    assert results[-1].success
    # After approval, the real form submitted and navigated to the result.
    assert "submitted.html" in agent.session.page.url

    agent.session.close()


def test_submit_rejected_does_not_navigate(tmp_path, local_server):
    orchestrator = create_orchestrator(str(tmp_path / "test2.db"))
    orchestrator.handle_user_message(
        f"open a browser session at {local_server}/login.html", request_id="r1"
    )
    agent = orchestrator.agents[AgentName.INTERACTIVE_BROWSER]

    try:
        orchestrator.handle_user_message("log in", request_id="r1")
        pytest.fail("Expected PendingConfirmation")
    except PendingConfirmation as e:
        pending = e

    results = orchestrator.resume_with_confirmation(pending.task, approved=False, request_id="r1")
    assert not results[-1].success
    assert "login.html" in agent.session.page.url  # never navigated
    agent.session.close()


def test_submit_falls_back_to_enter_when_button_is_disabled(tmp_path, local_server):
    """Regression test for a real bug found on DuckDuckGo: modern search
    sites have a submit button that's deliberately disabled and hidden
    (search fires on Enter). The old code found that button and timed out
    trying to click the unclickable thing. This form mirrors that exactly
    (disabled + display:none button); submit must fall through to pressing
    Enter and still navigate to the result page."""
    orchestrator = create_orchestrator(str(tmp_path / "test_disabled.db"))
    orchestrator.handle_user_message(
        f"open a browser session at {local_server}/search.html", request_id="r1"
    )
    orchestrator.handle_user_message('type "hello" into the search field', request_id="r1")
    agent = orchestrator.agents[AgentName.INTERACTIVE_BROWSER]

    try:
        orchestrator.handle_user_message("submit", request_id="r1")
        pytest.fail("Expected PendingConfirmation")
    except PendingConfirmation as e:
        pending = e

    results = orchestrator.resume_with_confirmation(pending.task, approved=True, request_id="r1")
    assert results[-1].success
    assert results[-1].data["method"] == "pressed Enter"
    assert "submitted.html" in agent.session.page.url
    agent.session.close()


def test_unrecognized_instruction_gives_helpful_message(agent):
    result = agent.handle(_task("do something browser-ish but vague"))
    assert not result.success
    assert "session" in result.output.lower()
