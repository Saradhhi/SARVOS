from agents.document_intent import classify, Operation, looks_like_document_request
from core.schemas import RiskLevel


def test_list_documents():
    assert classify("list documents").operation == Operation.LIST
    assert classify("show me my documents").operation == Operation.LIST


def test_read_variants():
    for text in ("read resume.pdf", "open resume.pdf",
                 "extract the text from resume.pdf", "show me resume.pdf"):
        i = classify(text)
        assert i.operation == Operation.READ, text
        assert i.filename == "resume.pdf"


def test_summarize_variants():
    for text in ("summarize contract.docx", "give me a summary of contract.docx",
                 "tldr contract.docx"):
        assert classify(text).operation == Operation.SUMMARIZE, text


def test_search_in_document():
    i = classify("search resume.pdf for python")
    assert i.operation == Operation.SEARCH
    assert i.filename == "resume.pdf"
    assert i.query == "python"


def test_find_in_variant():
    i = classify('find "salary" in contract.docx')
    assert i.operation == Operation.SEARCH
    assert i.filename == "contract.docx"
    assert i.query == "salary"


def test_everything_is_safe():
    """Reading changes nothing. The risk is disclosure, handled by the
    sandbox, not by a confirmation prompt."""
    for text in ("read resume.pdf", "summarize contract.docx", "list documents"):
        assert classify(text).risk == RiskLevel.SAFE


def test_traversal_parses_so_it_can_be_explicitly_refused():
    """Rejecting '../' by failing to match would be defense by accident --
    it leaves the real sandbox check untested."""
    i = classify("read ../../../etc/passwd.txt")
    assert i.operation == Operation.READ
    assert i.filename == "../../../etc/passwd.txt"


def test_subdirectories_work():
    assert classify("read contracts/2024.pdf").filename == "contracts/2024.pdf"


def test_a_filename_needs_an_extension():
    """Without this, 'read the room' becomes a document request."""
    assert classify("read the room").operation == Operation.UNKNOWN
    assert classify("open the door").operation == Operation.UNKNOWN
    assert classify("summarize the meeting").operation == Operation.UNKNOWN


def test_web_searches_are_not_document_searches():
    assert classify("search the web for python").operation == Operation.UNKNOWN
    assert classify("research quantum computing").operation == Operation.UNKNOWN


def test_unrelated_is_unknown():
    assert classify("what's the weather").operation == Operation.UNKNOWN


def test_looks_like_document_request():
    assert looks_like_document_request("read resume.pdf")
    assert not looks_like_document_request("remember that I like tea")
