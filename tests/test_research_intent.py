from agents.research_intent import classify, Operation, looks_like_research_request
from core.schemas import RiskLevel


def test_research_verb():
    intent = classify("research the history of Rome")
    assert intent.operation == Operation.SEARCH
    assert intent.risk == RiskLevel.SAFE
    assert intent.query == "the history of Rome"


def test_search_for_variant():
    intent = classify("search for the best pizza recipe")
    assert intent.operation == Operation.SEARCH
    assert intent.query == "the best pizza recipe"


def test_search_without_for():
    intent = classify("search python asyncio tutorial")
    assert intent.operation == Operation.SEARCH
    assert intent.query == "python asyncio tutorial"


def test_look_up_variant():
    intent = classify("look up the capital of France")
    assert intent.operation == Operation.SEARCH
    assert intent.query == "the capital of France"


def test_lookup_one_word_variant():
    intent = classify("lookup quantum computing basics")
    assert intent.operation == Operation.SEARCH


def test_find_information_about_variant():
    intent = classify("find information about climate change")
    assert intent.operation == Operation.SEARCH
    assert intent.query == "climate change"


def test_find_out_about_variant():
    intent = classify("find out about the new iphone")
    assert intent.operation == Operation.SEARCH
    assert intent.query == "the new iphone"


def test_strips_trailing_punctuation():
    intent = classify("search for the weather today?")
    assert intent.query == "the weather today"


def test_unrelated_instruction_is_unknown():
    intent = classify("what's the weather today")
    assert intent.operation == Operation.UNKNOWN


def test_empty_query_after_verb_is_unknown():
    intent = classify("search for")
    assert intent.operation == Operation.UNKNOWN


def test_looks_like_research_request():
    assert looks_like_research_request("research black holes")
    assert not looks_like_research_request("remember that I like tea")
