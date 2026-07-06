"""
Automation configuration. The single most important setting here is
WORKSPACE_ROOT: the ONLY directory tree SARVOS's file operations are
allowed to touch. This exists specifically to prevent "delete file
C:\\Windows\\System32\\something" or "read file ../../../.ssh/id_rsa" from
being possible even if the LLM-driven Planner routing or a user's phrasing
somehow produced such an instruction -- the boundary is enforced at the
path-resolution level (see agents/automation.py's _resolve_safe_path),
not just by hoping the instruction parsing never produces a bad path.

Defaults to a "sarvos_workspace" folder created next to wherever SARVOS is
run from -- deliberately NOT the user's whole home directory or Desktop,
so a first automation feature starts from the smallest reasonable blast
radius. Override via SARVOS_WORKSPACE_ROOT once you trust it more.
"""

from __future__ import annotations

import os

WORKSPACE_ROOT = os.environ.get("SARVOS_WORKSPACE_ROOT", "sarvos_workspace")

# Git commands run with this as the working directory (cwd), NOT
# WORKSPACE_ROOT -- git operations are about the SARVOS project repo
# itself (or whatever repo you point this at), a separate concern from
# the sandboxed file read/write/delete workspace above. Defaults to "."
# (wherever SARVOS is run from).
GIT_REPO_ROOT = os.environ.get("SARVOS_GIT_REPO_ROOT", ".")

# Hard cap on file size for read/write operations, so "read file
# huge_video.mp4" doesn't try to load gigabytes into memory or send it to
# an LLM.
MAX_FILE_SIZE_BYTES = int(os.environ.get("SARVOS_MAX_FILE_SIZE_BYTES", str(1_000_000)))  # 1MB

# Timeout for git subprocess calls, so a hung git operation (e.g. waiting
# on a credential prompt) can't hang SARVOS forever.
GIT_TIMEOUT_SECONDS = float(os.environ.get("SARVOS_GIT_TIMEOUT_SECONDS", "15"))
