from agents.browser_intent import classify, Operation, looks_like_browser_request
from core.schemas import RiskLevel


def test_open_website_basic():
    intent = classify("open website example.com")
    assert intent.operation == Operation.OPEN_URL
    assert intent.risk == RiskLevel.SAFE
    assert intent.url == "https://example.com"


def test_go_to_variant():
    intent = classify("go to github.com")
    assert intent.operation == Operation.OPEN_URL
    assert intent.url == "https://github.com"


def test_visit_variant_with_explicit_scheme():
    intent = classify("visit http://example.com")
    assert intent.url == "http://example.com"


def test_screenshot_request():
    intent = classify("take a screenshot of example.com")
    assert intent.operation == Operation.SCREENSHOT
    assert intent.url == "https://example.com"


def test_screenshot_of_variant_without_take_a():
    intent = classify("screenshot of wikipedia.org")
    assert intent.operation == Operation.SCREENSHOT


def test_file_scheme_is_blocked():
    """Critical safety check: file:// must never be treated as a valid
    browser target -- it would let page-reading double as an arbitrary
    local file reader, bypassing the file-automation agent's sandboxing
    entirely."""
    intent = classify("open website file:///etc/passwd")
    assert intent.operation == Operation.UNKNOWN


def test_javascript_scheme_is_blocked():
    intent = classify("open website javascript:alert(1)")
    assert intent.operation == Operation.UNKNOWN


def test_data_scheme_is_blocked():
    intent = classify("open website data:text/html,<script>alert(1)</script>")
    assert intent.operation == Operation.UNKNOWN


def test_mailto_scheme_is_blocked():
    intent = classify("open website mailto:someone@example.com")
    assert intent.operation == Operation.UNKNOWN


def test_ftp_scheme_is_blocked():
    intent = classify("open website ftp://example.com")
    assert intent.operation == Operation.UNKNOWN


def test_unrelated_instruction_is_unknown():
    intent = classify("what's the weather today")
    assert intent.operation == Operation.UNKNOWN


def test_looks_like_browser_request():
    assert looks_like_browser_request("open example.com")
    assert not looks_like_browser_request("remember that I like tea")


# ---- Real misrouting bug found in live use ------------------------------

def test_ordinary_questions_are_not_turned_into_urls():
    """Real bug: 'show me how to reverse a list in python' matched the
    open-URL phrasing, and the sentence was turned into
    'https://how to reverse a list in python' and navigated to
    (ERR_NAME_NOT_RESOLVED). Hostnames cannot contain whitespace."""
    for text in (
        "show me how to reverse a list in python",
        "show me what you can do",
        "open the pod bay doors",
        "visit my grandmother next week",
        "go to sleep",
    ):
        assert classify(text).operation == Operation.UNKNOWN, text


def test_real_urls_and_hosts_still_open():
    cases = {
        "open example.com": "https://example.com",
        "go to https://github.com": "https://github.com",
        "visit duckduckgo.com": "https://duckduckgo.com",
        "show me the website example.com": "https://example.com",
        "browse to news.ycombinator.com": "https://news.ycombinator.com",
    }
    for text, expected in cases.items():
        intent = classify(text)
        assert intent.operation == Operation.OPEN_URL, text
        assert intent.url == expected, text


def test_host_with_port_is_handled():
    """Regression: 'localhost:8000' has a colon, so a naive scheme check
    reads 'localhost' as a URI scheme and refuses it."""
    assert classify("open localhost:8000").url == "https://localhost:8000"
    assert classify("go to 127.0.0.1:5000").url == "https://127.0.0.1:5000"


def test_dangerous_schemes_remain_blocked_after_the_fix():
    """The host-shape check must not weaken the existing scheme blocking."""
    for text in (
        "open javascript:alert(1)",
        "open file:///etc/passwd",
        "open website file:///etc/passwd",
        "go to mailto:someone@example.com",
    ):
        assert classify(text).operation == Operation.UNKNOWN, text
