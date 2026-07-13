"""
Job assistant configuration -- multi-candidate.

Each candidate gets their own folder under CANDIDATES_DIR, holding their
profile, resume, saved postings, and application log together:

    candidates/
      alice/
        profile.json
        resume.docx          (or whatever the resume is named)
        postings/
        applications.json

This scales to many candidates while staying inspectable: one folder is one
person's whole job search. You can back it up, hand it off, or delete it
without touching anyone else -- the "just look at the files" property this
project values.

You set an ACTIVE candidate once ("use candidate alice") and every command
after applies to them, so daily use is switch-and-go rather than naming a
candidate on every line. The active candidate is remembered in a small
marker file so it survives restarts.
"""

from __future__ import annotations

import os
import re

# Root under which every candidate's folder lives.
CANDIDATES_DIR = os.environ.get("SARVOS_CANDIDATES_DIR", "sarvos_workspace/candidates")

# Remembers who the active candidate is, across restarts.
ACTIVE_CANDIDATE_FILE = os.environ.get(
    "SARVOS_ACTIVE_CANDIDATE", "sarvos_workspace/active_candidate.txt"
)

# Cap on how much posting/resume text goes to the model. Same reasoning as
# the document agent: don't blow the context window or truncate silently.
MAX_POSTING_CHARS = int(os.environ.get("SARVOS_MAX_POSTING_CHARS", "8000"))

# The fields a profile may contain. Explicit, so a typo'd key is caught
# rather than silently stored, and so the form-filler knows what to map.
PROFILE_FIELDS = (
    "full_name", "first_name", "last_name", "email", "phone",
    "location", "linkedin", "github", "portfolio", "website",
    "current_title", "years_experience", "work_authorization",
    "salary_expectation", "notice_period",
)

# Candidate names become folder names, so constrain them to something safe
# and predictable rather than trusting arbitrary input as a path component.
_VALID_CANDIDATE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$", re.I)


def is_valid_candidate_name(name: str) -> bool:
    return bool(_VALID_CANDIDATE.match(name or ""))

