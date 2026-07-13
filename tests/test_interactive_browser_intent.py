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


# ---- Tabs, downloads, PDF, bookmarks, snapshot compare ------------------

def test_tab_operations():
    assert classify("open a new tab").operation == Operation.NEW_TAB
    assert classify("new tab").operation == Operation.NEW_TAB
    assert classify("list tabs").operation == Operation.LIST_TABS
    assert classify("what tabs are open").operation == Operation.LIST_TABS


def test_new_tab_with_a_url():
    i = classify("open a new tab at example.com")
    assert i.operation == Operation.NEW_TAB
    assert i.url == "https://example.com"


def test_new_tab_with_nonsense_instead_of_a_url_is_unknown():
    """Same lesson as 'show me how to reverse a list in python' -- text that
    isn't shaped like a host must not become one."""
    assert classify("open a new tab at some point later").operation == Operation.UNKNOWN


def test_tab_numbers_are_one_based_for_humans_zero_based_inside():
    assert classify("switch to tab 1").tab_index == 0
    assert classify("switch to tab 3").tab_index == 2
    assert classify("close tab 2").tab_index == 1


def test_close_tab_is_sensitive_not_destructive():
    """These are SARVOS's own headless tabs -- at worst an unsubmitted form.
    Contrast the window agent's close, which can discard a person's unsaved
    work in an application they were actually using."""
    assert classify("close tab 1").risk == RiskLevel.SENSITIVE


def test_close_tab_does_not_shadow_closing_the_session():
    assert classify("close the browser session").operation == Operation.CLOSE
    assert classify("close tab 1").operation == Operation.CLOSE_TAB


def test_download_requires_quoted_link_text_or_a_filename():
    """Caught by its own routing test: the loose version stole 'download the
    latest version of python', which is a question, not a command."""
    assert classify('download "Get the report"').text_arg == "Get the report"
    assert classify("download report.pdf").text_arg == "report.pdf"
    for text in ("download the latest version of python", "download node",
                 "download whatever you want"):
        assert classify(text).operation == Operation.UNKNOWN, text


def test_bookmark_requires_an_explicit_name():
    """The loose version turned 'bookmark this for later' into a bookmark
    named 'this for later'."""
    assert classify("bookmark this page as hn").name_arg == "hn"
    assert classify("bookmark as hn").name_arg == "hn"
    for text in ("bookmark this for later", "bookmark that"):
        assert classify(text).operation == Operation.UNKNOWN, text


def test_download_and_pdf_are_sensitive():
    """Both write a file to disk."""
    assert classify("download report.pdf").risk == RiskLevel.SENSITIVE
    assert classify("save the page as pdf").risk == RiskLevel.SENSITIVE
    assert classify("print this page to pdf").operation == Operation.SAVE_PDF


def test_bookmark_operations():
    i = classify("bookmark this page as hackernews")
    assert i.operation == Operation.BOOKMARK
    assert i.name_arg == "hackernews"
    assert classify("list bookmarks").operation == Operation.LIST_BOOKMARKS
    assert classify("open bookmark hackernews").name_arg == "hackernews"


def test_open_bookmark_does_not_look_like_a_url_open():
    assert classify("open bookmark hn").operation == Operation.OPEN_BOOKMARK


def test_check_changes_variants():
    assert classify("check this page for changes").operation == Operation.CHECK_CHANGES
    assert classify("has the page changed").operation == Operation.CHECK_CHANGES


def test_everything_new_is_safe_except_writes_and_tab_close():
    for text in ("list tabs", "switch to tab 1", "open a new tab",
                 "list bookmarks", "check this page for changes"):
        assert classify(text).risk == RiskLevel.SAFE, text
