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

# --- File upload -------------------------------------------------------
# Uploads are restricted to this directory, resolved with the same
# parent-membership check as file automation (agents/automation.py's
# resolve_safe_path). Handing a file to a remote website is a data
# exfiltration path: without this, "upload ../../.ssh/id_rsa" would work.
# Put your resume here; nothing outside it can be uploaded.
UPLOAD_DIR = os.environ.get("SARVOS_UPLOAD_DIR", "sarvos_workspace/uploads")

# Completed-form previews (the dry-run screenshots) land here.
PREVIEW_DIR = os.environ.get("SARVOS_PREVIEW_DIR", "sarvos_workspace/previews")

# --- Downloads and saved pages ------------------------------------------
# Files pulled off the internet, and PDFs printed from pages, are written
# only here. Same reasoning as UPLOAD_DIR: a browser agent that can write to
# an arbitrary path on request is a remote-controlled file writer.
DOWNLOAD_DIR = os.environ.get("SARVOS_DOWNLOAD_DIR", "sarvos_workspace/downloads")
PDF_DIR = os.environ.get("SARVOS_PDF_DIR", "sarvos_workspace/pages")

# A downloaded file larger than this is refused. Chosen to be generous for
# documents and small archives while making a runaway download obvious.
MAX_DOWNLOAD_BYTES = int(os.environ.get("SARVOS_MAX_DOWNLOAD_BYTES", str(100 * 1024 * 1024)))

# How long to wait for a page to "settle" (DOM ready, then network quiet)
# after navigating, so JS-rendered content is present before we read it.
# Shorter than the hard load timeout: this wait is allowed to expire without
# being an error -- a settled-enough page beats hanging or failing.
SETTLE_TIMEOUT_MS = int(os.environ.get("SARVOS_SETTLE_TIMEOUT_MS", "8000"))

# networkidle is OFF by default: it holds the page open (many sites never go
# idle) and can exhaust browser resources under load, for little gain over
# domcontentloaded. Opt in for the rare SPA that needs it.
WAIT_NETWORKIDLE = os.environ.get("SARVOS_WAIT_NETWORKIDLE", "") == "1"
NETWORKIDLE_BUDGET_MS = int(os.environ.get("SARVOS_NETWORKIDLE_BUDGET_MS", "2500"))
