from agents.system_info_intent import classify, Operation, looks_like_system_info_request
from core.schemas import RiskLevel


def test_cpu_check():
    intent = classify("check my cpu usage")
    assert intent.operation == Operation.CPU
    assert intent.risk == RiskLevel.SAFE


def test_ram_check():
    intent = classify("how much ram do I have")
    assert intent.operation == Operation.RAM


def test_memory_synonym_for_ram():
    intent = classify("what's my memory usage")
    assert intent.operation == Operation.RAM


def test_disk_check():
    intent = classify("what's my disk usage")
    assert intent.operation == Operation.DISK


def test_storage_synonym_for_disk():
    intent = classify("how much storage do I have left")
    assert intent.operation == Operation.DISK


def test_battery_check():
    intent = classify("check my battery")
    assert intent.operation == Operation.BATTERY


def test_network_check():
    intent = classify("what's my network status")
    assert intent.operation == Operation.NETWORK


def test_general_system_info():
    intent = classify("system info")
    assert intent.operation == Operation.ALL


def test_hows_my_computer_variant():
    intent = classify("how's my computer doing")
    assert intent.operation == Operation.ALL


def test_system_status_variant():
    intent = classify("give me the system status")
    assert intent.operation == Operation.ALL


def test_unrelated_sentence_mentioning_memory_is_not_misrouted():
    """Critical negative case: a sentence that happens to contain a
    resource word ('memory') but isn't actually asking about system
    stats must not be misrouted here."""
    intent = classify("I need more memory for my project")
    assert intent.operation == Operation.UNKNOWN


def test_unrelated_sentence_mentioning_network_is_not_misrouted():
    intent = classify("can you help me network with other developers")
    assert intent.operation == Operation.UNKNOWN


def test_unrelated_instruction_is_unknown():
    intent = classify("what's the weather today")
    assert intent.operation == Operation.UNKNOWN


def test_looks_like_system_info_request():
    assert looks_like_system_info_request("check my cpu")
    assert not looks_like_system_info_request("remember that I like tea")
