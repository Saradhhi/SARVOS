"""
Configuration, read from environment variables. Every default here is
chosen to keep the free/local path the one that "just works" — you only
pay anything if you explicitly set SARVOS_LLM_BACKEND to a paid provider
(not implemented in this build; see llm/client.py's OllamaClient docstring
for what a paid backend would need to look like).
"""

from __future__ import annotations

import os

# "ollama" (free, local, default) is the only backend implemented in this
# build. A future paid backend (Anthropic/OpenAI) would read its own API key
# from the environment the same way — never hardcode a key in source.
LLM_BACKEND = os.environ.get("SARVOS_LLM_BACKEND", "ollama")

OLLAMA_HOST = os.environ.get("SARVOS_OLLAMA_HOST", "http://localhost:11434")

# llama3.2 is a reasonable small/fast default for a personal-assistant use
# case. Override with any model you've pulled: `ollama pull <model>` then
# `export SARVOS_OLLAMA_MODEL=<model>`.
OLLAMA_MODEL = os.environ.get("SARVOS_OLLAMA_MODEL", "llama3.2")

# Seconds to wait for Ollama before falling back to the stub response.
# Local inference on modest hardware can be slow on first load (model
# needs to be read into memory) — 30s avoids a false "unavailable" on a
# cold start while not hanging forever if Ollama really isn't running.
OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("SARVOS_OLLAMA_TIMEOUT", "30"))
