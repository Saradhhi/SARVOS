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


# ---- File upload (sandboxed) and dry-run preview -------------------------

_UPLOAD_HTML = """
<html><head><title>Job Application</title></head><body>
<h1>Apply</h1>
<form method="post" action="/submitted.html" enctype="multipart/form-data">
  <input type="text" name="fullname" aria-label="Full name" />
  <input type="password" name="pw" aria-label="Password" />
  <input type="file" name="resume" aria-label="Resume" />
  <button type="submit">Apply</button>
</form>
</body></html>
"""


@pytest.fixture
def upload_server(tmp_path):
    import http.server, threading
    d = tmp_path / "site"
    d.mkdir()
    (d / "apply.html").write_text(_UPLOAD_HTML)
    (d / "submitted.html").write_text(_SUBMITTED_HTML)
    h = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(d), **k)
    srv = http.server.HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    d = tmp_path / "uploads"
    d.mkdir()
    (d / "resume.pdf").write_bytes(b"%PDF-1.4 fake resume")
    monkeypatch.setattr("agents.browser_config.UPLOAD_DIR", str(d))
    monkeypatch.setattr("agents.browser_config.PREVIEW_DIR", str(tmp_path / "previews"))
    yield d


def test_upload_attaches_a_real_file(agent, upload_server, upload_dir):
    agent.handle(_task(f"open a browser session at {upload_server}/apply.html"))
    r = agent.handle(_task("upload resume.pdf to the resume field"))
    assert r.success, r.output
    # The real DOM now has the file attached.
    name = agent.session.page.evaluate(
        "() => document.querySelector(\"input[type='file']\").files[0].name"
    )
    assert name == "resume.pdf"


def test_upload_refuses_a_path_outside_the_upload_dir(agent, upload_server, upload_dir):
    """Handing a local file to a remote site is an exfiltration path.
    'upload ../../.ssh/id_rsa' must never work."""
    agent.handle(_task(f"open a browser session at {upload_server}/apply.html"))
    r = agent.handle(_task("upload ../../../etc/passwd.pdf to the resume field"))
    assert not r.success
    assert r.error in {"unsafe_upload_path", "upload_file_not_found"}


def test_upload_missing_file_fails_clearly(agent, upload_server, upload_dir):
    agent.handle(_task(f"open a browser session at {upload_server}/apply.html"))
    r = agent.handle(_task("upload nonexistent.pdf to the resume field"))
    assert not r.success
    assert r.error == "upload_file_not_found"


def test_upload_before_open_reports_no_session(agent, upload_dir):
    r = agent.handle(_task("upload resume.pdf to the resume field"))
    assert not r.success
    assert r.error == "no_session"


def test_preview_shows_real_field_values_and_masks_passwords(agent, upload_server, upload_dir):
    agent.handle(_task(f"open a browser session at {upload_server}/apply.html"))
    agent.handle(_task('type "Saradhi" into the fullname field'))
    agent.handle(_task('type "hunter2" into the pw field'))
    agent.handle(_task("upload resume.pdf to the resume field"))

    r = agent.handle(_task("preview the form"))
    assert r.success, r.output
    values = {f["name"]: f["value"] for f in r.data["fields"]}
    assert values["fullname"] == "Saradhi"
    assert values["pw"] == "********", "passwords must never be echoed"
    assert "hunter2" not in r.output
    assert values["resume"] == "resume.pdf"


def test_preview_writes_a_real_screenshot(agent, upload_server, upload_dir):
    from pathlib import Path as _P
    agent.handle(_task(f"open a browser session at {upload_server}/apply.html"))
    r = agent.handle(_task("preview the form"))
    assert r.success
    shot = _P(r.data["screenshot"])
    assert shot.is_file() and shot.stat().st_size > 0


def test_preview_is_safe_and_never_submits(agent, upload_server, upload_dir):
    """The whole point: previewing must not navigate anywhere."""
    agent.handle(_task(f"open a browser session at {upload_server}/apply.html"))
    before = agent.session.page.url
    agent.handle(_task("preview the form"))
    assert agent.session.page.url == before


# ---- The preview must not over-report ------------------------------------

_RADIO_HTML = """
<html><head><title>Order</title></head><body>
<form method="get" action="/submitted.html">
  <input type="text" name="custname" />
  <input type="radio" name="size" value="small" />
  <input type="radio" name="size" value="medium" />
  <input type="radio" name="size" value="large" />
  <input type="checkbox" name="topping" value="bacon" />
  <input type="checkbox" name="topping" value="cheese" />
  <select name="delivery">
    <option value="">--</option>
    <option value="asap">ASAP</option>
    <option value="later">Later</option>
  </select>
  <input type="text" name="ignored" disabled value="nope" />
  <button type="submit">Go</button>
</form></body></html>
"""


@pytest.fixture
def radio_server(tmp_path):
    import http.server, threading
    d = tmp_path / "radio"
    d.mkdir()
    (d / "order.html").write_text(_RADIO_HTML)
    (d / "submitted.html").write_text(_SUBMITTED_HTML)
    h = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(d), **k)
    srv = http.server.HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_preview_omits_unchecked_radios_and_checkboxes(agent, radio_server, upload_dir):
    """Real bug found by running the preview against httpbin: reading
    el.value on radios/checkboxes reports EVERY option, checked or not. The
    preview claimed 'size: small/medium/large' would be submitted when
    nothing was selected. A preview that over-reports is worse than none."""
    agent.handle(_task(f"open a browser session at {radio_server}/order.html"))
    agent.handle(_task('type "Saradhi" into the custname field'))

    r = agent.handle(_task("preview the form"))
    assert r.success
    names = [f["name"] for f in r.data["fields"]]
    assert "custname" in names
    assert "size" not in names, "unchecked radios must not appear"
    assert "topping" not in names, "unchecked checkboxes must not appear"
    assert "delivery" not in names, "an empty select must not appear"
    assert "ignored" not in names, "disabled inputs are never submitted"


def test_preview_reports_checked_values_only(agent, radio_server, upload_dir):
    agent.handle(_task(f"open a browser session at {radio_server}/order.html"))
    page = agent.session.page
    page.check("input[name='size'][value='medium']")
    page.check("input[name='topping'][value='cheese']")
    page.select_option("select[name='delivery']", "asap")

    r = agent.handle(_task("preview the form"))
    values = {f["name"]: f["value"] for f in r.data["fields"]}
    assert values["size"] == "medium"
    assert values["topping"] == "cheese"
    assert values["delivery"] == "asap"


def test_preview_matches_what_the_server_actually_receives(agent, radio_server, upload_dir):
    """The only assertion that really matters: does the preview tell the
    truth? Compare it against the real query string the server sees."""
    from urllib.parse import urlparse, parse_qs

    agent.handle(_task(f"open a browser session at {radio_server}/order.html"))
    agent.handle(_task('type "Saradhi" into the custname field'))
    agent.session.page.check("input[name='size'][value='large']")

    preview = agent.handle(_task("preview the form"))
    promised = {f["name"]: f["value"] for f in preview.data["fields"]}

    agent.session.page.click("button[type='submit']")
    agent.session.page.wait_for_load_state()
    actually_sent = {k: v[0] for k, v in parse_qs(urlparse(agent.session.page.url).query).items()}

    assert promised == actually_sent, (
        f"preview promised {promised} but server received {actually_sent}"
    )


# ---- No false successes on a page with nothing to act on ----------------

_RESULTS_HTML = """
<html><head><title>Result</title></head><body>
<pre>{"form": {"size": "large", "topping": "bacon"}}</pre>
<p>Your order was large with bacon.</p>
</body></html>
"""


@pytest.fixture
def results_server(tmp_path):
    import http.server, threading
    d = tmp_path / "results"
    d.mkdir()
    (d / "result.html").write_text(_RESULTS_HTML)
    h = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(d), **k)
    srv = http.server.HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_click_does_not_match_plain_text(agent, results_server):
    """Real bug: on httpbin's JSON results page, 'click large' matched the
    word "large" inside the response body and reported "Clicked 'large'."
    Nothing was clicked. Text is not a button."""
    agent.handle(_task(f"open a browser session at {results_server}/result.html"))
    r = agent.handle(_task("click large"))
    assert not r.success
    assert r.error == "element_not_found"


def test_click_still_finds_real_labels_and_buttons(agent, radio_server, upload_dir):
    """The fix must not break clicking things that ARE clickable."""
    agent.handle(_task(f"open a browser session at {radio_server}/order.html"))
    # The form has a real submit button.
    assert agent.handle(_task("click Go")).success


def test_click_finds_radio_by_label(agent, upload_server, upload_dir):
    agent.handle(_task(f"open a browser session at {upload_server}/apply.html"))
    # aria-label="Full name" is a real, labelled input.
    r = agent.handle(_task("click Full name"))
    assert r.success


def test_submit_refuses_a_page_with_no_form(tmp_path, results_server):
    """And refuses BEFORE the confirmation gate, so you're never asked to
    approve submitting nothing."""
    orchestrator = create_orchestrator(str(tmp_path / "nf.db"))
    orchestrator.handle_user_message(
        f"open a browser session at {results_server}/result.html", request_id="r1"
    )
    agent = orchestrator.agents[AgentName.INTERACTIVE_BROWSER]

    results = orchestrator.handle_user_message("submit", request_id="r1")
    assert not results[-1].success
    assert results[-1].error == "no_form_to_submit"
    agent.session.close()


def test_submit_still_gates_a_real_form(tmp_path, radio_server, upload_dir):
    """The preflight must only refuse, never weaken the gate."""
    orchestrator = create_orchestrator(str(tmp_path / "yf.db"))
    orchestrator.handle_user_message(
        f"open a browser session at {radio_server}/order.html", request_id="r1"
    )
    agent = orchestrator.agents[AgentName.INTERACTIVE_BROWSER]
    before = agent.session.page.url

    try:
        orchestrator.handle_user_message("submit", request_id="r1")
        pytest.fail("a real form must still be gated")
    except PendingConfirmation as e:
        pending = e
    assert agent.session.page.url == before

    orchestrator.resume_with_confirmation(pending.task, approved=True, request_id="r1")
    assert "submitted.html" in agent.session.page.url
    agent.session.close()
