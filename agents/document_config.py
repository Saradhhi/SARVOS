"""
Document intelligence configuration.
"""

from __future__ import annotations

import os

# Documents SARVOS may READ. Deliberately separate from browser_config's
# UPLOAD_DIR ("files SARVOS may SEND to a website"), even though both sit
# under sarvos_workspace. Collapsing them would mean dropping a file in one
# place silently grants both permissions -- you should be able to let SARVOS
# read a confidential contract without also letting it upload one.
DOCUMENTS_DIR = os.environ.get("SARVOS_DOCUMENTS_DIR", "sarvos_workspace/documents")

# Extracted text is capped before it reaches a chat response or, worse, a
# voice response to be read aloud. Same reasoning as browser_config.
MAX_TEXT_LENGTH = int(os.environ.get("SARVOS_MAX_DOC_TEXT_LENGTH", "4000"))

# How much of a document goes to the LLM for summarizing. A model asked to
# summarize content it only partially saw will confidently summarize the
# part it got -- the truncation must be visible, never silent.
MAX_SUMMARY_CHARS = int(os.environ.get("SARVOS_MAX_SUMMARY_CHARS", "12000"))

# Refuse outright above this: a document this large can't be summarized
# honestly without chunking, which isn't built yet.
MAX_FILE_BYTES = int(os.environ.get("SARVOS_MAX_DOC_BYTES", str(20 * 1024 * 1024)))
