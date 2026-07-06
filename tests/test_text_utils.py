from voice.text_utils import split_into_sentences


def test_splits_basic_sentences():
    result = split_into_sentences("This is one. This is two. This is three!")
    assert result == ["This is one.", "This is two.", "This is three!"]


def test_handles_question_marks():
    result = split_into_sentences("How's it going? I'm doing well.")
    assert result == ["How's it going?", "I'm doing well."]


def test_single_sentence_no_split():
    result = split_into_sentences("Just one sentence here.")
    assert result == ["Just one sentence here."]


def test_empty_string_returns_empty_list():
    assert split_into_sentences("") == []
    assert split_into_sentences("   ") == []


def test_no_trailing_punctuation_still_returned():
    result = split_into_sentences("First one. Second one without punctuation")
    assert result == ["First one.", "Second one without punctuation"]
