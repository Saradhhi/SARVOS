"""
Tests for guards added after a real session where the chat LLM fabricated
convincing prose about file changes that never happened -- twice.
"""

from agents.general import SPOKEN_SYSTEM_PROMPT, SYSTEM_PROMPT
from main import is_stray_confirmation


def test_bare_affirmatives_are_stray_confirmations():
    """Real bug: after 'propose a fix' (SAFE, asks nothing), a stray 'y'
    reached the general agent, which improvised prose claiming it had
    applied the patch. Nothing had been written."""
    assert is_stray_confirmation("y")
    assert is_stray_confirmation("yes")
    assert is_stray_confirmation("Y")
    assert is_stray_confirmation("YES")


def test_bare_negatives_are_stray_confirmations():
    assert is_stray_confirmation("n")
    assert is_stray_confirmation("no")
    assert is_stray_confirmation("No")


def test_whitespace_tolerated():
    assert is_stray_confirmation("  y  ")


def test_real_questions_are_not_stray_confirmations():
    """Critical negative case: a real message that merely starts with or
    contains 'no'/'yes' must still reach the assistant normally."""
    assert not is_stray_confirmation("no idea what that means")
    assert not is_stray_confirmation("yes, but what about the tests?")
    assert not is_stray_confirmation("nothing works")
    assert not is_stray_confirmation("apply the fix")
    assert not is_stray_confirmation("")


def test_system_prompt_forbids_inventing_file_contents():
    """The general agent has no filesystem access. It must be explicitly
    told never to invent file contents or claim a change was applied --
    both of which it really did before this guard existed."""
    lowered = SYSTEM_PROMPT.lower()
    assert "no access" in lowered
    assert "never invent file contents" in lowered
    assert "never say a change has been applied" in lowered


def test_spoken_prompt_also_forbids_inventing_file_contents():
    """Voice mode carries the identical risk."""
    lowered = SPOKEN_SYSTEM_PROMPT.lower()
    assert "no access" in lowered
    assert "never invent file contents" in lowered


# ---- Enforcing the no-fabricated-diffs rule in CODE, not just prompt ----
#
# A system prompt is a request, not a constraint. Confirmed directly on a
# real machine: told categorically never to show a before/after block of
# the user's real files, llama3.2 said "You can't see the contents of
# calc.py" and then displayed a diff of that exact file anyway.

from agents.general import strip_fabricated_diffs


REAL_FABRICATED_RESPONSE = """You can't see the contents of calc.py. To verify, run `cat calc.py`.
If you want to review the changes made, I can show you the difference:
```
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
-    def add(a, b):
-        return a - b
+    def add(a, b):
+        return a + b
```"""


def test_strips_the_exact_diff_the_model_really_fabricated():
    """Regression test using the model's verbatim real output. Note the
    reconstructed diff even had the indentation wrong -- plausible, and
    false."""
    cleaned = strip_fabricated_diffs(REAL_FABRICATED_RESPONSE)
    assert "--- a/calc.py" not in cleaned
    assert "+++ b/calc.py" not in cleaned
    assert "return a - b" not in cleaned
    assert "SARVOS removed a diff" in cleaned
    # The honest part of the answer survives.
    assert "can't see the contents" in cleaned


def test_strips_unfenced_diff_lines():
    text = "Here's the change:\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x\n+y\n"
    cleaned = strip_fabricated_diffs(text)
    assert "--- a/foo.py" not in cleaned
    assert "SARVOS removed a diff" in cleaned


def test_leaves_legitimate_code_examples_alone():
    """The general agent may absolutely answer coding questions. It just may
    never claim to show the contents of the user's real files."""
    text = "Reverse a list:\n```python\nitems = [1, 2]\nitems.reverse()\n```\nDone."
    assert strip_fabricated_diffs(text) == text.strip()


def test_leaves_plain_prose_untouched():
    text = "The capital of France is Paris."
    assert strip_fabricated_diffs(text) == text


def test_no_diff_markers_is_a_fast_passthrough():
    text = "Nothing diff-like here at all, even with -- dashes and @ signs."
    assert strip_fabricated_diffs(text) == text
