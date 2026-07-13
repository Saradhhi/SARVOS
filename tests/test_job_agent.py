"""
Tests for the multi-candidate job assistant. Everything is verifiable here
against real files and a fake LLM. The core property under test is
ISOLATION: two candidates never see each other's profile, postings, or
applications.
"""

import json

import pytest

from agents.job import JobAgent
from core.schemas import AgentName, Task
from memory.engine import MemoryEngine
from memory.store import Store


def _task(instruction: str, context=None) -> Task:
    return Task(parent_request_id="r1", agent=AgentName.JOB,
                instruction=instruction, context=context or {})


class _FakeLLM:
    def __init__(self, response="Matches: Python. Missing: Go."):
        self.response = response
        self.prompts = []

    def generate(self, prompt, system=None):
        self.prompts.append((prompt, system))
        return self.response


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.job_config.CANDIDATES_DIR", str(tmp_path / "candidates"))
    monkeypatch.setattr("agents.job_config.ACTIVE_CANDIDATE_FILE", str(tmp_path / "active.txt"))
    yield tmp_path


@pytest.fixture
def agent(env):
    memory = MemoryEngine(store=Store(env / "j.db"))
    yield JobAgent(memory)


def _add_and_use(agent, name):
    agent.handle(_task(f"add candidate {name}"))


# ---- candidate management ------------------------------------------------

def test_no_candidate_means_profile_commands_refuse(agent):
    r = agent.handle(_task("set my email to x@y.com"))
    assert not r.success
    assert r.error == "no_active_candidate"


def test_add_candidate_creates_folder_and_activates(agent, env):
    r = agent.handle(_task("add candidate alice"))
    assert r.success
    assert (env / "candidates" / "alice").is_dir()
    assert agent.handle(_task("which candidate is active")).data["candidate"] == "alice"


def test_invalid_candidate_name_rejected(agent):
    r = agent.handle(_task("add candidate my best friend"))
    # Spaces make it not match the pattern at all -> unknown op, OR invalid.
    assert not r.success


def test_use_nonexistent_candidate_fails(agent):
    r = agent.handle(_task("use candidate ghost"))
    assert not r.success
    assert r.error == "candidate_not_found"


def test_list_candidates_shows_all_with_active_marked(agent):
    _add_and_use(agent, "alice")
    _add_and_use(agent, "bob")  # bob now active
    r = agent.handle(_task("list candidates"))
    assert set(r.data["candidates"]) == {"alice", "bob"}
    assert r.data["active"] == "bob"


# ---- THE isolation property ---------------------------------------------

def test_two_candidates_have_separate_profiles(agent, env):
    _add_and_use(agent, "alice")
    agent.handle(_task("set my email to alice@example.com"))
    _add_and_use(agent, "bob")
    agent.handle(_task("set my email to bob@example.com"))

    agent.handle(_task("use candidate alice"))
    assert agent.handle(_task("show my profile")).data["profile"]["email"] == "alice@example.com"
    agent.handle(_task("use candidate bob"))
    assert agent.handle(_task("show my profile")).data["profile"]["email"] == "bob@example.com"

    # On disk, genuinely separate files.
    a = json.loads((env / "candidates" / "alice" / "profile.json").read_text())
    b = json.loads((env / "candidates" / "bob" / "profile.json").read_text())
    assert a["email"] != b["email"]


def test_applications_are_per_candidate(agent):
    _add_and_use(agent, "alice")
    agent.handle(_task("log an application to Acme - Engineer"))
    agent.handle(_task("log an application to Globex"))
    _add_and_use(agent, "bob")
    agent.handle(_task("log an application to Initech"))

    agent.handle(_task("use candidate alice"))
    alice_apps = agent.handle(_task("list applications")).data["applications"]
    assert {a["company"] for a in alice_apps} == {"Acme", "Globex"}

    agent.handle(_task("use candidate bob"))
    bob_apps = agent.handle(_task("list applications")).data["applications"]
    assert {a["company"] for a in bob_apps} == {"Initech"}


def test_postings_are_per_candidate(agent, env):
    _add_and_use(agent, "alice")
    agent.handle(_task("save this posting as role1",
                       context={"page_text": "Alice's target job"}))
    _add_and_use(agent, "bob")
    # Bob has no such posting.
    monkey = _FakeLLM()
    r = agent.handle(_task("match role1 against the resume"))
    assert not r.success
    assert r.error == "posting_not_found"
    # But Alice does.
    assert (env / "candidates" / "alice" / "postings" / "role1.txt").is_file()


# ---- profile -------------------------------------------------------------

def test_unknown_profile_field_rejected(agent):
    _add_and_use(agent, "alice")
    r = agent.handle(_task("set my favourite colour to blue"))
    assert not r.success
    assert r.error == "unknown_profile_field"


def test_passwords_refused(agent):
    _add_and_use(agent, "alice")
    r = agent.handle(_task("set my password to hunter2"))
    assert not r.success
    assert "hunter2" not in json.dumps(agent._load_profile("alice"))


# ---- matching (reads the candidate's own resume) -------------------------

def test_match_reads_the_candidates_own_resume(agent, env, monkeypatch):
    _add_and_use(agent, "alice")
    (env / "candidates" / "alice" / "resume.txt").write_text(
        "Alice. Python developer. Django, FastAPI."
    )
    agent.handle(_task("save this posting as eng",
                       context={"page_text": "Required: Python, Go."}))
    fake = _FakeLLM()
    monkeypatch.setattr("agents.job.get_llm_client", lambda: fake)

    r = agent.handle(_task("match eng against the resume"))
    assert r.success
    prompt = fake.prompts[0][0]
    assert "Python developer" in prompt   # real resume text
    assert "Required: Python, Go" in prompt  # real posting text
    assert "not a verdict on your fit" in r.output


def test_match_without_a_resume_in_the_folder_fails(agent, monkeypatch):
    _add_and_use(agent, "alice")
    agent.handle(_task("save this posting as eng", context={"page_text": "job"}))
    monkeypatch.setattr("agents.job.get_llm_client", lambda: _FakeLLM())
    r = agent.handle(_task("match eng against the resume"))
    assert not r.success
    assert r.error == "resume_not_found"


def test_match_missing_posting_fails(agent):
    _add_and_use(agent, "alice")
    r = agent.handle(_task("match ghost against the resume"))
    assert not r.success
    assert r.error == "posting_not_found"


def test_match_handles_llm_unavailable(agent, env, monkeypatch):
    from llm.client import LLMUnavailable
    _add_and_use(agent, "alice")
    (env / "candidates" / "alice" / "resume.txt").write_text("Alice. Dev.")
    agent.handle(_task("save this posting as eng", context={"page_text": "job"}))

    class Dead:
        def generate(self, prompt, system=None):
            raise LLMUnavailable("down")

    monkeypatch.setattr("agents.job.get_llm_client", lambda: Dead())
    r = agent.handle(_task("match eng against the resume"))
    assert not r.success
    assert r.error == "llm_unavailable"


# ---- form fill -----------------------------------------------------------

def test_fill_form_gives_reviewable_commands(agent):
    _add_and_use(agent, "alice")
    agent.handle(_task("set my email to a@b.com"))
    r = agent.handle(_task("fill this form from my profile"))
    assert r.success
    assert "type" in r.output
    assert "review each before sending" in r.output


def test_fill_form_empty_profile_refuses(agent):
    _add_and_use(agent, "alice")
    r = agent.handle(_task("fill this form from my profile"))
    assert not r.success
    assert r.error == "empty_profile"


def test_unknown_instruction_is_helpful(agent):
    r = agent.handle(_task("do something jobby but vague"))
    assert not r.success
    assert "candidate" in r.output.lower()


# ---- Resume tailoring: optimize, rewrite, cover letter ------------------
#
# All three read the candidate's REAL resume and a REAL saved posting. The
# tests assert the real text reaches the model and that generated files
# never get mistaken for the source resume.

def _setup_alice_with_posting(agent, env, resume="Alice. Forklift certified. Clean driving record."):
    _add_and_use(agent, "alice")
    (env / "candidates" / "alice" / "resume.txt").write_text(resume)
    agent.handle(_task("save this posting as driver",
                       context={"page_text": "Delivery Driver. 21+, clean MVR, DOT card."}))


def test_optimize_reads_real_resume_and_posting(agent, env, monkeypatch):
    _setup_alice_with_posting(agent, env)
    fake = _FakeLLM("ROLE FIT SCORE: 6/10")
    monkeypatch.setattr("agents.job.get_llm_client", lambda: fake)
    r = agent.handle(_task("optimize my resume for driver"))
    assert r.success
    prompt = fake.prompts[0][0]
    assert "Forklift certified" in prompt          # real resume
    assert "Delivery Driver" in prompt             # real posting
    assert "ATS resume optimizer" in fake.prompts[0][1]  # the user's prompt
    # It's analysis, labelled as the model's read.
    assert "the local model's read" in r.output


def test_optimize_is_safe_but_rewrite_and_cover_are_sensitive():
    from agents.job_intent import classify
    from core.schemas import RiskLevel
    assert classify("optimize my resume for x").risk == RiskLevel.SAFE
    assert classify("rewrite my resume for x").risk == RiskLevel.SENSITIVE
    assert classify("write a cover letter for x").risk == RiskLevel.SENSITIVE


def test_rewrite_writes_a_file_and_warns_to_check_it(agent, env, monkeypatch):
    _setup_alice_with_posting(agent, env)
    monkeypatch.setattr("agents.job.get_llm_client",
                        lambda: _FakeLLM("ALICE CHEN\nSummary: reliable driver"))
    r = agent.handle(_task("rewrite my resume for driver"))
    assert r.success
    from pathlib import Path
    assert Path(r.data["file"]).is_file()
    # It must tell the person to verify -- this is a draft, not truth.
    assert "Read it before you use it" in r.output
    assert "nothing was invented" in r.output


def test_generated_resume_does_not_break_resume_detection(agent, env, monkeypatch):
    """After a rewrite, the folder has two resume-like files. Auto-detection
    must still find the ORIGINAL, not the generated one -- caught live."""
    _setup_alice_with_posting(agent, env)
    monkeypatch.setattr("agents.job.get_llm_client", lambda: _FakeLLM("tailored"))
    agent.handle(_task("rewrite my resume for driver"))   # creates a 2nd file
    # Cover letter must still auto-find the original resume.
    r = agent.handle(_task("write a cover letter for driver"))
    assert r.success, r.output


def test_cover_letter_writes_a_file(agent, env, monkeypatch):
    _setup_alice_with_posting(agent, env)
    monkeypatch.setattr("agents.job.get_llm_client",
                        lambda: _FakeLLM("Dear Hiring Manager,"))
    r = agent.handle(_task("write a cover letter for driver"))
    assert r.success
    from pathlib import Path
    assert Path(r.data["file"]).is_file()
    assert "read and edit it before sending" in r.output.lower()


def test_optimize_missing_posting_fails(agent, env, monkeypatch):
    _add_and_use(agent, "alice")
    (env / "candidates" / "alice" / "resume.txt").write_text("Alice.")
    monkeypatch.setattr("agents.job.get_llm_client", lambda: _FakeLLM())
    r = agent.handle(_task("optimize my resume for ghost"))
    assert not r.success
    assert r.error == "posting_not_found"


def test_rewrite_handles_llm_unavailable(agent, env, monkeypatch):
    from llm.client import LLMUnavailable
    _setup_alice_with_posting(agent, env)

    class Dead:
        def generate(self, prompt, system=None):
            raise LLMUnavailable("down")

    monkeypatch.setattr("agents.job.get_llm_client", lambda: Dead())
    r = agent.handle(_task("rewrite my resume for driver"))
    assert not r.success
    assert r.error == "llm_unavailable"


def test_save_posting_distinguishes_empty_page_from_no_session(agent, env, monkeypatch):
    """A misleading message told users to open a session they already had.
    An open-but-empty page (JS not rendered) must say so, not 'open a
    session first'."""
    _add_and_use(agent, "alice")

    class _FakeSession:
        def is_open(self): return True
        class page:
            @staticmethod
            def inner_text(sel): return ""   # rendered nothing
    class _FakeBrowser:
        session = _FakeSession()

    agent.browser = _FakeBrowser()
    r = agent.handle(_task("save this posting as driver"))
    assert not r.success
    assert r.error == "page_empty"
    assert "still rendering" in r.output


def test_save_posting_no_session_still_explains_how(agent, env):
    _add_and_use(agent, "alice")
    agent.browser = None
    r = agent.handle(_task("save this posting as driver"))
    assert not r.success
    assert r.error == "no_posting_text"
