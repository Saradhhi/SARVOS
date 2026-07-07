from voice.assistant import is_stop_command


def test_recognizes_bare_stop():
    assert is_stop_command("stop")


def test_recognizes_never_mind_variants():
    assert is_stop_command("never mind")
    assert is_stop_command("nevermind")


def test_recognizes_cancel():
    assert is_stop_command("cancel")
    assert is_stop_command("cancel that")


def test_case_insensitive():
    assert is_stop_command("STOP")
    assert is_stop_command("Never Mind")


def test_tolerates_trailing_punctuation():
    assert is_stop_command("stop.")
    assert is_stop_command("stop!")
    assert is_stop_command("never mind?")


def test_tolerates_surrounding_whitespace():
    assert is_stop_command("  stop  ")


def test_critical_negative_case_real_question_containing_stop_word():
    """THE important case: a real question that happens to contain the
    word 'stop' must NEVER be mistaken for a cancel command -- this has
    to be a whole-utterance match, not a substring check."""
    assert not is_stop_command("how do I stop a car")
    assert not is_stop_command("what's the stop sign rule")
    assert not is_stop_command("can you help me stop smoking")
    assert not is_stop_command("cancel my subscription to netflix")


def test_unrelated_utterances_are_not_stop_commands():
    assert not is_stop_command("tell me about the sun")
    assert not is_stop_command("what's the weather today")
    assert not is_stop_command("")
