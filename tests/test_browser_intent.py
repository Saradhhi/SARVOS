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
