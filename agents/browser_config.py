"""
Browser automation configuration.
"""

from __future__ import annotations

import os

# Screenshots are saved here, sandboxed the same way file automation is --
# not written to an arbitrary path the instruction happens to mention.
SCREENSHOT_DIR = os.environ.get("SARVOS_SCREENSHOT_DIR", "sarvos_workspace/screenshots")

PAGE_LOAD_TIMEOUT_MS = int(os.environ.get("SARVOS_PAGE_LOAD_TIMEOUT_MS", "15000"))

# Extracted page text is capped so a huge page doesn't get dumped whole
# into a chat response (or, worse, into a voice response to be read aloud).
MAX_TEXT_LENGTH = int(os.environ.get("SARVOS_MAX_PAGE_TEXT_LENGTH", "3000"))

HEADLESS = os.environ.get("SARVOS_BROWSER_HEADLESS", "1") == "1"
