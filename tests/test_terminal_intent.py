from agents.terminal_intent import classify, Operation, looks_like_terminal_request
from core.schemas import RiskLevel


def test_running_processes():
    intent = classify("show me the running processes")
    assert intent.operation == Operation.PROCESSES
    assert intent.risk == RiskLevel.SAFE


def test_list_processes_variant():
    intent = classify("list processes")
    assert intent.operation == Operation.PROCESSES


def test_whats_running_variant():
    intent = classify("what's running on my computer")
    assert intent.operation == Operation.PROCESSES


def test_whoami():
    intent = classify("whoami")
    assert intent.operation == Operation.CURRENT_USER


def test_who_am_i_variant():
    intent = classify("who am i")
    assert intent.operation == Operation.CURRENT_USER


def test_current_user_variant():
    intent = classify("what's the current user")
    assert intent.operation == Operation.CURRENT_USER


def test_hostname():
    intent = classify("what's my hostname")
    assert intent.operation == Operation.HOSTNAME


def test_computer_name_variant():
    intent = classify("what's my computer name")
    assert intent.operation == Operation.HOSTNAME


def test_os_version():
    intent = classify("what os version am I running")
    assert intent.operation == Operation.OS_VERSION


def test_windows_version_variant():
    intent = classify("what's my windows version")
    assert intent.operation == Operation.OS_VERSION


def test_unrelated_instruction_is_unknown():
    intent = classify("what's the weather today")
    assert intent.operation == Operation.UNKNOWN


def test_does_not_enable_arbitrary_command_phrasing():
    """Critical scope test: phrasing that sounds like a raw command
    request must NOT be interpreted as something to execute -- this
    agent only recognizes the fixed, safe diagnostic set, never
    arbitrary commands."""
    intent = classify("run rm -rf /")
    assert intent.operation == Operation.UNKNOWN
    intent2 = classify("execute del *.* /s")
    assert intent2.operation == Operation.UNKNOWN


def test_looks_like_terminal_request():
    assert looks_like_terminal_request("whoami")
    assert not looks_like_terminal_request("remember that I like tea")
