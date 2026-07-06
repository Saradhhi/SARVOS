from agents.automation_intent import classify, Operation, looks_like_automation_request
from core.schemas import RiskLevel


def test_read_file_is_safe():
    intent = classify("read file notes.txt")
    assert intent.operation == Operation.READ_FILE
    assert intent.risk == RiskLevel.SAFE
    assert intent.path == "notes.txt"


def test_show_file_variant():
    intent = classify("show the file report.md")
    assert intent.operation == Operation.READ_FILE
    assert intent.path == "report.md"


def test_list_directory_is_safe():
    intent = classify("list the files in projects")
    assert intent.operation == Operation.LIST_DIR
    assert intent.risk == RiskLevel.SAFE
    assert intent.path == "projects"


def test_write_file_is_sensitive():
    intent = classify("write a file called todo.txt with buy milk")
    assert intent.operation == Operation.WRITE_FILE
    assert intent.risk == RiskLevel.SENSITIVE
    assert intent.path == "todo.txt"
    assert intent.content == "buy milk"


def test_delete_file_is_destructive():
    intent = classify("delete the file old_notes.txt")
    assert intent.operation == Operation.DELETE_FILE
    assert intent.risk == RiskLevel.DESTRUCTIVE
    assert intent.path == "old_notes.txt"


def test_git_status_is_safe():
    intent = classify("git status")
    assert intent.operation == Operation.GIT_COMMAND
    assert intent.risk == RiskLevel.SAFE
    assert intent.git_args == ["status"]


def test_git_log_is_safe():
    intent = classify("git log")
    assert intent.risk == RiskLevel.SAFE


def test_git_commit_is_sensitive():
    intent = classify("git commit -m fix bug")
    assert intent.operation == Operation.GIT_COMMAND
    assert intent.risk == RiskLevel.SENSITIVE
    assert intent.git_args[0] == "commit"


def test_git_push_is_destructive():
    intent = classify("git push")
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_git_reset_is_destructive():
    intent = classify("git reset --hard")
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_unrecognized_git_subcommand_is_treated_as_destructive():
    """Allowlist, not a denylist: anything not explicitly known is gated
    as if it were destructive, even though the agent will separately
    refuse to actually run it."""
    intent = classify("git something-made-up")
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_unrelated_instruction_is_unknown():
    intent = classify("what's the weather today")
    assert intent.operation == Operation.UNKNOWN


def test_looks_like_automation_request_true_cases():
    assert looks_like_automation_request("read file x.txt")
    assert looks_like_automation_request("git status")
    assert looks_like_automation_request("delete the file y.txt")


def test_looks_like_automation_request_false_case():
    assert not looks_like_automation_request("tell me a joke")
