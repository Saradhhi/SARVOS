from agents.window_manager_intent import classify, Operation, looks_like_window_request
from core.schemas import RiskLevel


def test_list_windows_is_safe():
    i = classify("list windows")
    assert i.operation == Operation.LIST
    assert i.risk == RiskLevel.SAFE


def test_list_open_windows_variant():
    assert classify("show me all open windows").operation == Operation.LIST
    assert classify("list my windows").operation == Operation.LIST


def test_active_window_is_safe():
    i = classify("what's the active window")
    assert i.operation == Operation.ACTIVE
    assert i.risk == RiskLevel.SAFE


def test_focus_is_sensitive():
    i = classify("focus on notepad")
    assert i.operation == Operation.FOCUS
    assert i.risk == RiskLevel.SENSITIVE
    assert i.target == "notepad"


def test_switch_to_variant():
    i = classify("switch to chrome")
    assert i.operation == Operation.FOCUS
    assert i.target == "chrome"


def test_minimize_with_target():
    i = classify("minimize notepad")
    assert i.operation == Operation.MINIMIZE
    assert i.risk == RiskLevel.SENSITIVE
    assert i.target == "notepad"


def test_minimize_without_target_means_active_window():
    i = classify("minimize")
    assert i.operation == Operation.MINIMIZE
    assert i.target is None


def test_the_active_window_resolves_to_none_target():
    i = classify("minimize the active window")
    assert i.operation == Operation.MINIMIZE
    assert i.target is None


def test_trailing_window_noun_is_stripped():
    i = classify("minimize the notepad window")
    assert i.target == "notepad"


def test_maximize_and_restore():
    assert classify("maximize notepad").operation == Operation.MAXIMIZE
    assert classify("restore notepad").operation == Operation.RESTORE
    assert classify("unminimize notepad").operation == Operation.RESTORE


def test_british_spelling_accepted():
    assert classify("minimise notepad").operation == Operation.MINIMIZE
    assert classify("maximise notepad").operation == Operation.MAXIMIZE


def test_move_extracts_coordinates():
    i = classify("move notepad to 100, 200")
    assert i.operation == Operation.MOVE
    assert i.risk == RiskLevel.SENSITIVE
    assert i.target == "notepad"
    assert (i.x, i.y) == (100, 200)


def test_move_accepts_negative_coordinates():
    """Multi-monitor setups genuinely have negative screen coordinates."""
    i = classify("move notepad to -1920, 0")
    assert (i.x, i.y) == (-1920, 0)


def test_resize_extracts_dimensions():
    i = classify("resize notepad to 800x600")
    assert i.operation == Operation.RESIZE
    assert (i.width, i.height) == (800, 600)


def test_resize_by_and_comma_variants():
    assert classify("resize notepad to 800 by 600").width == 800
    assert classify("resize notepad to 800, 600").height == 600


def test_resize_rejects_zero_or_negative():
    assert classify("resize notepad to 0x600").operation == Operation.UNKNOWN


def test_close_window_is_destructive():
    i = classify("close the notepad window")
    assert i.operation == Operation.CLOSE
    assert i.risk == RiskLevel.DESTRUCTIVE
    assert i.target == "notepad"


def test_close_bare_window_targets_active():
    i = classify("close window")
    assert i.operation == Operation.CLOSE
    assert i.target is None


def test_critical_negatives_for_close():
    """A false positive on close loses unsaved work. Ordinary sentences must
    never reach it -- the same lesson as the 'develop' substring bug and the
    'show me' misrouting."""
    for text in (
        "close the topic",
        "close the deal",
        "close enough",
        "let's close out this discussion",
    ):
        assert classify(text).operation == Operation.UNKNOWN, text


def test_critical_negatives_for_other_verbs():
    for text in (
        "focus on your work",
        "I need to focus",
        "move on to the next task",
        "restore my faith in humanity",
        "minimize the risk",
    ):
        assert classify(text).operation == Operation.UNKNOWN, text


def test_unrelated_instruction_is_unknown():
    assert classify("what's the weather today").operation == Operation.UNKNOWN


def test_looks_like_window_request():
    assert looks_like_window_request("list windows")
    assert not looks_like_window_request("remember that I like tea")
