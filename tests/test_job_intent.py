from agents.job_intent import classify, Operation, looks_like_job_request
from core.schemas import RiskLevel


def test_set_profile_is_sensitive():
    i = classify("set my email to x@y.com")
    assert i.operation == Operation.SET_PROFILE
    assert i.risk == RiskLevel.SENSITIVE
    assert i.field == "email"
    assert i.value == "x@y.com"


def test_set_profile_variants():
    assert classify("set profile phone to 555").field == "phone"
    assert classify("set my full name to Saradhi Meka").value == "Saradhi Meka"


def test_show_profile():
    assert classify("show my profile").operation == Operation.SHOW_PROFILE
    assert classify("view profile").operation == Operation.SHOW_PROFILE


def test_save_and_match_posting():
    assert classify("save this posting as senior-eng").value == "senior-eng"
    i = classify("match senior-eng against my resume")
    assert i.operation == Operation.MATCH_POSTING
    assert i.value == "senior-eng"


def test_match_with_explicit_resume():
    i = classify("match x against my resume sfdc.docx")
    assert i.field == "sfdc.docx"


def test_fill_form():
    assert classify("fill this form from my profile").operation == Operation.FILL_FORM
    assert classify("fill the application with my profile").operation == Operation.FILL_FORM


def test_log_and_list_applications():
    assert classify("log an application to Acme").value == "Acme"
    assert classify("log application: Acme - Senior Eng").value == "Acme - Senior Eng"
    assert classify("list applications").operation == Operation.LIST_APPLICATIONS


def test_everything_safe_except_setting_profile():
    for text in ("show my profile", "save this posting as x",
                 "match x against my resume", "list applications",
                 "log an application to Y"):
        assert classify(text).risk == RiskLevel.SAFE, text
    assert classify("set my email to a@b.com").risk == RiskLevel.SENSITIVE


def test_false_positives_do_not_match():
    """These merely share verbs. None is a job command."""
    for text in ("set a reminder for tomorrow", "match the socks",
                 "fill my water bottle", "show me the news",
                 "save the file report.txt", "log the error"):
        assert classify(text).operation == Operation.UNKNOWN, text


def test_looks_like_job_request():
    assert looks_like_job_request("show my profile")
    assert not looks_like_job_request("what's the weather")


# ---- candidate management ------------------------------------------------

def test_candidate_operations():
    assert classify("add candidate alice").candidate == "alice"
    assert classify("create candidate bob").operation == Operation.ADD_CANDIDATE
    assert classify("use candidate alice").candidate == "alice"
    assert classify("switch to candidate bob").operation == Operation.USE_CANDIDATE
    assert classify("list candidates").operation == Operation.LIST_CANDIDATES
    assert classify("which candidate is active").operation == Operation.WHOAMI


def test_whoami_does_not_steal_the_terminal_command():
    """Bare 'whoami' is the terminal agent's (OS user). The job agent only
    claims the candidate-specific phrasings."""
    assert classify("whoami").operation == Operation.UNKNOWN


def test_candidate_names_must_be_folder_safe():
    assert classify("add candidate my best friend").operation == Operation.UNKNOWN


def test_match_accepts_her_his_their_resume():
    assert classify("match x against her resume").operation == Operation.MATCH_POSTING
    assert classify("match x against the resume").operation == Operation.MATCH_POSTING
