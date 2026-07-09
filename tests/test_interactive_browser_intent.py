from agents.interactive_browser_intent import classify, Operation, looks_like_interactive_browser_request
from core.schemas import RiskLevel


def test_open_session():
    intent = classify("open a browser session at example.com")
    assert intent.operation == Operation.OPEN
    assert intent.url == "https://example.com"
    assert intent.risk == RiskLevel.SAFE


def test_start_browsing_variant():
    intent = classify("start browsing github.com")
    assert intent.operation == Operation.OPEN
    assert intent.url == "https://github.com"


def test_open_session_blocks_non_http_scheme():
    intent = classify("open a browser session at file:///etc/passwd")
    # file:// is refused by _normalize_url -> falls through to UNKNOWN
    assert intent.operation == Operation.UNKNOWN


def test_type_into_field():
    intent = classify('type "hello@example.com" into the email field')
    assert intent.operation == Operation.TYPE
    assert intent.text_arg == "hello@example.com"
    assert intent.field_arg == "email"
    assert intent.risk == RiskLevel.SAFE


def test_type_in_variant():
    intent = classify('type "myusername" in username')
    assert intent.operation == Operation.TYPE
    assert intent.text_arg == "myusername"
    assert intent.field_arg == "username"


def test_read_page():
    intent = classify("read the page")
    assert intent.operation == Operation.READ


def test_submit_is_destructive():
    intent = classify("submit the form")
    assert intent.operation == Operation.SUBMIT
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_login_is_destructive():
    intent = classify("log in")
    assert intent.operation == Operation.SUBMIT
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_signin_variant_is_destructive():
    intent = classify("sign in")
    assert intent.operation == Operation.SUBMIT
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_click_is_safe():
    intent = classify('click "the next button"')
    assert intent.operation == Operation.CLICK
    assert intent.text_arg == "the next button"
    assert intent.risk == RiskLevel.SAFE


def test_click_without_quotes():
    intent = classify("click the accept cookies button")
    assert intent.operation == Operation.CLICK
    assert intent.text_arg == "accept cookies button"


def test_close_session():
    intent = classify("close the browser session")
    assert intent.operation == Operation.CLOSE


def test_submit_wins_over_loose_click_pattern():
    """The click pattern is deliberately loose (matches almost anything
    after 'click'). Submit/read/type/etc. must all be checked BEFORE
    click so they aren't swallowed by it. 'submit the form' must be
    SUBMIT (destructive), never accidentally a click."""
    intent = classify("submit the form")
    assert intent.operation == Operation.SUBMIT
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_unrelated_instruction_is_unknown():
    intent = classify("what's the weather today")
    assert intent.operation == Operation.UNKNOWN


def test_looks_like_interactive_browser_request():
    assert looks_like_interactive_browser_request("read the page")
    assert not looks_like_interactive_browser_request("remember that I like tea")
