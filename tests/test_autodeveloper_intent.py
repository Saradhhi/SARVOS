from agents.autodeveloper_intent import classify, Operation, looks_like_autodeveloper_request
from core.schemas import RiskLevel


def test_analyze_workspace():
    intent = classify("analyze the workspace")
    assert intent.operation == Operation.ANALYZE
    assert intent.risk == RiskLevel.SAFE


def test_analyze_project_variant():
    intent = classify("analyze project")
    assert intent.operation == Operation.ANALYZE


def test_run_tests():
    intent = classify("run the tests")
    assert intent.operation == Operation.RUN_TESTS
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_run_test_suite_variant():
    intent = classify("run the test suite")
    assert intent.operation == Operation.RUN_TESTS


def test_deploy():
    intent = classify("deploy the project")
    assert intent.operation == Operation.DEPLOY
    assert intent.risk == RiskLevel.DESTRUCTIVE


def test_bare_deploy():
    intent = classify("deploy")
    assert intent.operation == Operation.DEPLOY


def test_unrelated_instruction_is_unknown():
    intent = classify("what's the weather today")
    assert intent.operation == Operation.UNKNOWN


def test_critical_negative_case_develop_as_ordinary_word():
    """THE critical regression test: the original integration routed on
    `if 'develop' in text.lower()`, which would have incorrectly matched
    all of these completely ordinary sentences that have nothing to do
    with running tests or deploying code."""
    assert classify("let's develop this idea further").operation == Operation.UNKNOWN
    assert classify("I want to develop my skills").operation == Operation.UNKNOWN
    assert classify("how do children develop language").operation == Operation.UNKNOWN
    assert classify("this is a developing situation").operation == Operation.UNKNOWN


def test_looks_like_autodeveloper_request():
    assert looks_like_autodeveloper_request("run the tests")
    assert not looks_like_autodeveloper_request("remember that I like tea")
