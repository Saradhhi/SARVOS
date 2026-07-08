from agents.computer_control_intent import classify, Operation, looks_like_computer_control_request
from core.schemas import RiskLevel


def test_screenshot():
    intent = classify("take a screenshot")
    assert intent.operation == Operation.SCREENSHOT
    assert intent.risk == RiskLevel.SAFE


def test_read_clipboard():
    intent = classify("what's in my clipboard")
    assert intent.operation == Operation.READ_CLIPBOARD


def test_write_clipboard_extracts_quoted_text():
    intent = classify("copy 'hello world' to the clipboard")
    assert intent.operation == Operation.WRITE_CLIPBOARD
    assert intent.text_arg == "hello world"
    assert intent.risk == RiskLevel.SAFE


def test_write_clipboard_set_variant():
    intent = classify('set the clipboard to "my address"')
    assert intent.operation == Operation.WRITE_CLIPBOARD
    assert intent.text_arg == "my address"


def test_lock_computer():
    intent = classify("lock my computer")
    assert intent.operation == Operation.LOCK
    assert intent.risk == RiskLevel.SAFE


def test_mute():
    intent = classify("mute")
    assert intent.operation == Operation.MUTE
    assert intent.risk == RiskLevel.SENSITIVE


def test_unmute():
    intent = classify("unmute")
    assert intent.operation == Operation.UNMUTE


def test_volume_up():
    intent = classify("turn the volume up")
    assert intent.operation == Operation.VOLUME_UP


def test_volume_down():
    intent = classify("lower the volume")
    assert intent.operation == Operation.VOLUME_DOWN


def test_set_volume_extracts_percentage():
    intent = classify("set the volume to 50%")
    assert intent.operation == Operation.SET_VOLUME
    assert intent.numeric_arg == 50
    assert intent.risk == RiskLevel.SENSITIVE


def test_set_volume_without_percent_sign():
    intent = classify("set volume to 75")
    assert intent.numeric_arg == 75


def test_brightness_up():
    intent = classify("increase the brightness")
    assert intent.operation == Operation.BRIGHTNESS_UP


def test_set_brightness_extracts_percentage():
    intent = classify("set the brightness to 30%")
    assert intent.operation == Operation.SET_BRIGHTNESS
    assert intent.numeric_arg == 30


def test_launch_app_extracts_name():
    intent = classify("launch notepad")
    assert intent.operation == Operation.LAUNCH_APP
    assert intent.text_arg == "notepad"
    assert intent.risk == RiskLevel.SENSITIVE


def test_launch_app_with_application_word():
    intent = classify("launch the application chrome")
    assert intent.operation == Operation.LAUNCH_APP
    assert intent.text_arg == "chrome"


def test_close_app_extracts_name():
    intent = classify("close the application notepad")
    assert intent.operation == Operation.CLOSE_APP
    assert intent.text_arg == "notepad"
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_quit_variant():
    intent = classify("quit spotify")
    assert intent.operation == Operation.CLOSE_APP
    assert intent.text_arg == "spotify"


def test_shutdown():
    intent = classify("shut down my computer")
    assert intent.operation == Operation.SHUTDOWN
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_restart():
    intent = classify("restart my computer")
    assert intent.operation == Operation.RESTART
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_sleep():
    intent = classify("put my computer to sleep")
    assert intent.operation == Operation.SLEEP
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_unrelated_instruction_is_unknown():
    intent = classify("what's the weather today")
    assert intent.operation == Operation.UNKNOWN


def test_critical_negative_cases_for_destructive_operations():
    """The most important tests in this file: ordinary sentences must
    never be misrouted into close-app, given how severe a false positive
    would be for these operations."""
    assert classify("I want to close the topic").operation == Operation.UNKNOWN
    assert classify("quitting time, see you tomorrow").operation == Operation.UNKNOWN
    assert classify("this restart of the project failed").operation == Operation.UNKNOWN
    assert classify("let's shut that idea down").operation == Operation.UNKNOWN


def test_does_not_enable_keyboard_or_mouse_simulation():
    """Scope test: this agent must never recognize keyboard/mouse
    simulation requests -- explicitly out of scope."""
    assert classify("press the enter key").operation == Operation.UNKNOWN
    assert classify("click at position 100 200").operation == Operation.UNKNOWN
    assert classify("type hello world").operation == Operation.UNKNOWN


def test_looks_like_computer_control_request():
    assert looks_like_computer_control_request("take a screenshot")
    assert not looks_like_computer_control_request("remember that I like tea")
