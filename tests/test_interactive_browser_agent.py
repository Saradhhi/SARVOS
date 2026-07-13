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


# ---- Tabs, downloads, PDF, bookmarks, snapshot compare ------------------

_TABS_HTML = """
<html><head><title>Home</title></head><body>
<h1>Home Page</h1>
<a href="report.txt" download="report.txt">Get the report</a>
<a href="other.html">Just a link</a>
</body></html>
"""
_OTHER_HTML = "<html><head><title>Other</title></head><body><p>Other page</p></body></html>"


@pytest.fixture
def tabs_server(tmp_path):
    import http.server, threading
    d = tmp_path / "tabsite"
    d.mkdir()
    (d / "home.html").write_text(_TABS_HTML)
    (d / "other.html").write_text(_OTHER_HTML)
    (d / "report.txt").write_text("quarterly numbers, all fine")
    h = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(d), **k)
    srv = http.server.HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def browser_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.browser_config.DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setattr("agents.browser_config.PDF_DIR", str(tmp_path / "pages"))
    yield tmp_path


def test_new_tab_and_list_and_switch(agent, tabs_server):
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    r = agent.handle(_task(f"open a new tab at {tabs_server}/other.html"))
    assert r.success
    assert len(agent.session.pages) == 2
    assert agent.session.page.title() == "Other"   # new tab is active

    r = agent.handle(_task("list tabs"))
    assert r.success
    assert r.data["active"] == 2
    assert [t["title"] for t in r.data["tabs"]] == ["Home", "Other"]

    r = agent.handle(_task("switch to tab 1"))
    assert r.success
    assert agent.session.page.title() == "Home"
    agent.session.close()


def test_switch_to_a_tab_that_does_not_exist(agent, tabs_server):
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    r = agent.handle(_task("switch to tab 5"))
    assert not r.success
    assert r.error == "no_such_tab"
    agent.session.close()


def test_close_tab_leaves_the_others(agent, tabs_server):
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    agent.handle(_task(f"open a new tab at {tabs_server}/other.html"))
    r = agent.handle(_task("close tab 2"))
    assert r.success
    assert len(agent.session.pages) == 1
    assert agent.session.page.title() == "Home"
    agent.session.close()


def test_closing_the_last_tab_ends_the_session_and_says_so(agent, tabs_server):
    """A zombie session with no tabs would be an invisible lie about state."""
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    r = agent.handle(_task("close tab 1"))
    assert r.success
    assert r.data["session_closed"] is True
    assert not agent.session.is_open()


def test_download_saves_a_real_file_into_the_sandbox(agent, tabs_server, browser_dirs):
    from pathlib import Path as _P
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    r = agent.handle(_task('download "Get the report"'))
    assert r.success, r.output
    f = _P(r.data["file"])
    assert f.is_file()
    assert f.read_text() == "quarterly numbers, all fine"
    # And it landed inside the sandbox, not anywhere else.
    assert str(browser_dirs / "downloads") in str(f)
    agent.session.close()


def test_download_from_a_plain_link_fails_honestly(agent, tabs_server, browser_dirs):
    """A link that merely navigates is not a download. Saying 'downloaded'
    would be a false success."""
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    r = agent.handle(_task('download "Just a link"'))
    assert not r.success
    assert r.error == "download_failed"
    agent.session.close()


def test_a_hostile_suggested_filename_cannot_escape_the_sandbox(agent):
    """The REMOTE SERVER chooses the download filename. A server suggesting
    '../../.ssh/authorized_keys' must not be able to write there."""
    safe = agent._safe_download_name("../../.ssh/authorized_keys")
    assert "/" not in safe and "\\" not in safe and ".." not in safe
    assert safe == "authorized_keys"
    assert agent._safe_download_name("") == "download"
    assert agent._safe_download_name("...") == "download"


def test_save_page_as_pdf_writes_a_real_pdf(agent, tabs_server, browser_dirs):
    from pathlib import Path as _P
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    r = agent.handle(_task("save the page as pdf"))
    assert r.success, r.output
    f = _P(r.data["file"])
    assert f.is_file()
    assert f.read_bytes().startswith(b"%PDF"), "must be a genuine PDF, not an empty file"
    agent.session.close()


def test_bookmarks_round_trip(agent, tabs_server):
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    r = agent.handle(_task("bookmark this page as home"))
    assert r.success
    assert "not in your real browser" in r.output

    r = agent.handle(_task("list bookmarks"))
    assert r.data["bookmarks"][0]["name"] == "home"

    agent.handle(_task(f"open a new tab at {tabs_server}/other.html"))
    r = agent.handle(_task("open bookmark home"))
    assert r.success
    assert agent.session.page.title() == "Home"
    agent.session.close()


def test_opening_a_missing_bookmark_fails_clearly(agent):
    r = agent.handle(_task("open bookmark nonexistent"))
    assert not r.success
    assert r.error == "no_such_bookmark"


def test_check_changes_first_time_says_there_is_nothing_to_compare(agent, tabs_server):
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    r = agent.handle(_task("check this page for changes"))
    assert r.success
    assert r.data["first_snapshot"] is True
    assert "nothing to compare" in r.output
    agent.session.close()


def test_check_changes_detects_a_real_change(agent, tabs_server):
    """Tests the 'changed' branch deterministically by seeding an old
    snapshot directly, rather than rewriting a served file and reloading.
    The earlier version did the latter and was flaky on Windows: the rewrite
    shared a filesystem-timestamp tick with the original, SimpleHTTPRequest-
    Handler returned 304 Not Modified, the browser served the CACHED old
    page, and change detection correctly reported 'unchanged'. The code was
    right; the test depended on cache-invalidation timing. Seeding the store
    avoids HTTP entirely."""
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    url = agent.session.page.url

    # Seed a snapshot whose hash cannot match the current page.
    agent.memory.store.save_page_snapshot(url, "old-hash-that-wont-match", 1)

    r = agent.handle(_task("check this page for changes"))
    assert r.success
    assert r.data["changed"] is True
    assert "CHANGED" in r.output
    # Honest about its own limits: it knows THAT it changed, not WHAT.
    assert "not what changed" in r.output
    agent.session.close()


def test_check_changes_reports_unchanged(agent, tabs_server):
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    agent.handle(_task("check this page for changes"))
    agent.session.page.reload()
    r = agent.handle(_task("check this page for changes"))
    assert r.success
    assert r.data["changed"] is False
    assert "Unchanged" in r.output
    agent.session.close()


def test_tab_operations_need_a_session(agent):
    for cmd in ("list tabs", "switch to tab 1", "close tab 1", "save the page as pdf"):
        r = agent.handle(_task(cmd))
        assert not r.success and r.error == "no_session", cmd


# ---- Inspect and autofill on a realistically MESSY form -----------------
#
# Models the things that break naive form-filling on real ATS portals
# (Workday/Greenhouse/iCIMS): fields named 'input-42' with the real label
# elsewhere, aria-label instead of a <label>, a required marker via
# aria-required, a <select> with options, and a field the profile can't map.

_MESSY_FORM_HTML = """
<html><head><title>Apply</title></head><body>
<form method="post" action="/submitted.html">
  <label for="fld-1">Full name</label>
  <input id="fld-1" name="input-42" type="text" required />

  <input name="input-43" type="email" aria-label="Email Address" aria-required="true" />

  <label>Phone
    <input name="input-44" type="tel" />
  </label>

  <label for="fld-4">Work Authorization</label>
  <select id="fld-4" name="input-45" required>
    <option value="">Select</option>
    <option value="citizen">US Citizen</option>
    <option value="visa">Requires Visa</option>
  </select>

  <label for="fld-5">Why do you want this role?</label>
  <textarea id="fld-5" name="input-46"></textarea>

  <input name="referral" type="text" placeholder="Who referred you?" />
  <button type="submit">Apply</button>
</form></body></html>
"""


@pytest.fixture
def messy_server(tmp_path):
    import http.server, threading
    d = tmp_path / "messy"
    d.mkdir()
    (d / "apply.html").write_text(_MESSY_FORM_HTML)
    (d / "submitted.html").write_text(_SUBMITTED_HTML)
    h = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(d), **k)
    srv = http.server.HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_inspect_resolves_real_labels_behind_opaque_names(agent, messy_server):
    """The whole point: on a real form the name is 'input-42' and the label
    lives in a <label for>, aria-label, or wrapping <label>. Inspect must
    surface the human label, not the opaque name."""
    agent.handle(_task(f"open a browser session at {messy_server}/apply.html"))
    r = agent.handle(_task("inspect the form"))
    assert r.success
    by_name = {f["name"]: f for f in r.data["fields"]}

    assert by_name["input-42"]["label"] == "Full name"          # label[for]
    assert by_name["input-43"]["label"] == "Email Address"      # aria-label
    assert "Phone" in by_name["input-44"]["label"]              # wrapping label
    assert by_name["input-45"]["label"] == "Work Authorization" # select label
    assert by_name["referral"]["label"] == "Who referred you?"  # placeholder


def test_inspect_reports_required_and_options(agent, messy_server):
    agent.handle(_task(f"open a browser session at {messy_server}/apply.html"))
    r = agent.handle(_task("inspect the form"))
    by_name = {f["name"]: f for f in r.data["fields"]}

    assert by_name["input-42"]["required"] is True             # required attr
    assert by_name["input-43"]["required"] is True             # aria-required
    assert by_name["input-44"]["required"] is False
    assert by_name["input-45"]["options"] == ["citizen", "visa"]  # empty option dropped
    assert r.data["required_count"] == 3


def test_inspect_needs_a_session(agent):
    r = agent.handle(_task("inspect the form"))
    assert not r.success
    assert r.error == "no_session"


# ---- Autofill: fill the confident, flag the rest, submit nothing --------
#
# _autofill was referenced in the handler dispatch but NEVER DEFINED -- the
# same dead-reference bug as the automation agent's SHELL_COMMAND. These
# tests exist so that can't silently happen again.

@pytest.fixture
def candidate_env(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.job_config.CANDIDATES_DIR", str(tmp_path / "candidates"))
    monkeypatch.setattr("agents.job_config.ACTIVE_CANDIDATE_FILE", str(tmp_path / "active.txt"))
    from agents.job import JobAgent
    from memory.engine import MemoryEngine
    from memory.store import Store
    job = JobAgent(MemoryEngine(store=Store(tmp_path / "j.db")))
    # Build a candidate with a realistic profile.
    d = tmp_path / "candidates" / "alice" / "postings"
    d.mkdir(parents=True)
    (tmp_path / "candidates" / "alice" / "profile.json").write_text(
        '{"full_name": "Alice Chen", "email": "alice@example.com", '
        '"phone": "555-0100", "current_title": "Backend Engineer"}'
    )
    (tmp_path / "active.txt").write_text("alice")
    yield tmp_path


def test_autofill_fills_confident_fields_and_flags_the_rest(agent, messy_server, candidate_env):
    agent.handle(_task(f"open a browser session at {messy_server}/apply.html"))
    r = agent.handle(_task("autofill from alice"))
    assert r.success

    filled = {f["profile_key"] for f in r.data["filled"]}
    # Name, email, phone all map to free-text fields -> filled.
    assert {"full_name", "email", "phone"} <= filled

    # The required <select> (Work Authorization) can't be guessed -> flagged.
    req_labels = {s["label"] for s in r.data["skipped_required"]}
    assert "Work Authorization" in req_labels
    assert any(s["reason"] == "needs a choice" for s in r.data["skipped_required"])

    # The free-text 'referral' has no profile match -> listed as unmapped,
    # not silently ignored.
    unmapped_labels = {u["label"] for u in r.data["unmapped"]}
    assert "Who referred you?" in unmapped_labels


def test_autofill_actually_types_into_the_page(agent, messy_server, candidate_env):
    """Fills must land in the DOM, not just be reported."""
    agent.handle(_task(f"open a browser session at {messy_server}/apply.html"))
    agent.handle(_task("autofill from alice"))
    page = agent.session.page
    assert page.input_value("input[name='input-42']") == "Alice Chen"
    assert page.input_value("input[name='input-43']") == "alice@example.com"
    assert page.input_value("input[name='input-44']") == "555-0100"
    # The unmapped and select fields stay empty -- nothing invented.
    assert page.input_value("input[name='referral']") == ""


def test_autofill_never_submits(agent, messy_server, candidate_env):
    """The whole safety premise: autofill types, it does not submit."""
    agent.handle(_task(f"open a browser session at {messy_server}/apply.html"))
    r = agent.handle(_task("autofill from alice"))
    assert "Nothing was submitted" in r.output
    # Still on the form, not the success page.
    assert agent.session.page.title() == "Apply"


def test_autofill_uses_active_candidate_when_none_named(agent, messy_server, candidate_env):
    agent.handle(_task(f"open a browser session at {messy_server}/apply.html"))
    r = agent.handle(_task("autofill"))
    assert r.success
    assert r.data["candidate"] == "alice"


def test_autofill_without_a_candidate_fails(agent, messy_server, tmp_path, monkeypatch):
    monkeypatch.setattr("agents.job_config.CANDIDATES_DIR", str(tmp_path / "none"))
    monkeypatch.setattr("agents.job_config.ACTIVE_CANDIDATE_FILE", str(tmp_path / "no_active.txt"))
    agent.handle(_task(f"open a browser session at {messy_server}/apply.html"))
    r = agent.handle(_task("autofill"))
    assert not r.success
    assert r.error == "no_candidate"


def test_autofill_with_empty_profile_fails(agent, messy_server, tmp_path, monkeypatch):
    monkeypatch.setattr("agents.job_config.CANDIDATES_DIR", str(tmp_path / "candidates"))
    monkeypatch.setattr("agents.job_config.ACTIVE_CANDIDATE_FILE", str(tmp_path / "active.txt"))
    (tmp_path / "candidates" / "bob" / "postings").mkdir(parents=True)
    (tmp_path / "candidates" / "bob" / "profile.json").write_text("{}")
    (tmp_path / "active.txt").write_text("bob")
    agent.handle(_task(f"open a browser session at {messy_server}/apply.html"))
    r = agent.handle(_task("autofill from bob"))
    assert not r.success
    assert r.error == "empty_profile"


def test_autofill_needs_a_session(agent, candidate_env):
    r = agent.handle(_task("autofill from alice"))
    assert not r.success
    assert r.error == "no_session"


# ---- Page settle (wait for JS-rendered content) -------------------------

def test_open_waits_for_the_dom_to_settle(agent, tabs_server, monkeypatch):
    """Plain goto() returns before a single-page app renders, which is why
    'read the page' came back empty on a JS-heavy job aggregator. _settle
    must be called on open so content is present before we read it."""
    seen = []
    real_settle = agent._settle
    monkeypatch.setattr(agent, "_settle",
                        lambda page: (seen.append(True), real_settle(page)))
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    assert seen, "_settle was not called on open"
    agent.session.close()


def test_read_settles_before_reading(agent, tabs_server, monkeypatch):
    agent.handle(_task(f"open a browser session at {tabs_server}/home.html"))
    seen = []
    real_settle = agent._settle
    monkeypatch.setattr(agent, "_settle",
                        lambda page: (seen.append(True), real_settle(page)))
    agent.handle(_task("read the page"))
    assert seen, "_settle was not called on read"
    agent.session.close()


def test_settle_tolerates_a_timeout_without_raising(agent):
    """A page that never settles must not turn into an error -- we read what
    rendered. Simulated with a fake page whose wait always times out."""
    class _StubPage:
        def wait_for_load_state(self, state, timeout=None):
            raise Exception("Timeout exceeded")
    # Should not raise.
    agent._settle(_StubPage())
