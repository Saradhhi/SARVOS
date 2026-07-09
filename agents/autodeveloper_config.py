"""
AutoDeveloper agent config.

Real fix from reviewing the original integration: NO user-supplied file
paths are accepted anywhere in this agent (unlike the original
DeveloperTools' read_file/write_file, which had a path-traversal bug --
`str.startswith()` on an absolute path string, which a sibling directory
like "workspace_evil/" also satisfies without being inside "workspace/"
at all). Every operation here works against a FIXED workspace root and
FIXED, admin-configured commands -- eliminating that entire attack
surface rather than trying to patch it.
"""

from __future__ import annotations

import os

WORKSPACE_ROOT = os.environ.get("SARVOS_AUTODEV_WORKSPACE", "autodeveloper_workspace")

TEST_COMMAND = os.environ.get("SARVOS_AUTODEV_TEST_COMMAND", "pytest")

DEPLOY_COMMAND = os.environ.get("SARVOS_AUTODEV_DEPLOY_COMMAND", "echo Deploy finished")

COMMAND_TIMEOUT_SECONDS = float(os.environ.get("SARVOS_AUTODEV_TIMEOUT_SECONDS", "120"))

# --- Auto-heal (propose/apply patch) ------------------------------------
# How much of the real test-failure output to include in the LLM prompt.
# Capped so a huge traceback doesn't blow past the model's context window.
MAX_TEST_OUTPUT_CHARS = int(os.environ.get("SARVOS_AUTODEV_MAX_TEST_OUTPUT", "4000"))

# How large a source file may be before we refuse to send it to the LLM
# for patching. Keeps prompts sane and avoids silently truncating a file
# (which would make the model propose a patch against content it never
# fully saw -- a genuinely dangerous failure mode).
MAX_PATCH_FILE_CHARS = int(os.environ.get("SARVOS_AUTODEV_MAX_PATCH_FILE", "20000"))
