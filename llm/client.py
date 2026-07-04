"""
LLM client abstraction.

One interface (`LLMClient.generate`), one implementation for now
(`OllamaClient`) — free, local, no API key, no per-token cost. Agents call
this through `get_llm_client()` and MUST catch `LLMUnavailable` and fall
back to a clearly-labeled stub response rather than crashing — Ollama not
being installed/running should degrade gracefully, not break the CLI.

Adding a paid backend later (Anthropic/OpenAI) means writing one more class
implementing the same `generate(prompt, system) -> str` interface and
selecting it via `config.LLM_BACKEND` — nothing calling `get_llm_client()`
needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import requests

from llm import config


class LLMUnavailable(Exception):
    """Raised when the configured LLM backend can't be reached at all
    (e.g. Ollama isn't installed or isn't running) — distinct from the
    backend being reachable but erroring, so callers can give a more useful
    message ("start Ollama" vs. "something went wrong")."""


class LLMClient(ABC):
    @abstractmethod
    def generate(self, prompt: str, system: str | None = None) -> str:
        """Return the model's full response text. Raises LLMUnavailable if
        the backend can't be reached; other errors propagate as-is."""
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap check of whether the backend is reachable right now,
        without incurring a full generation call."""
        raise NotImplementedError


class OllamaClient(LLMClient):
    def __init__(
        self,
        host: str = config.OLLAMA_HOST,
        model: str = config.OLLAMA_MODEL,
        timeout: float = config.OLLAMA_TIMEOUT_SECONDS,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=2)
            return resp.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload: dict = {"model": self.model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system

        try:
            resp = requests.post(
                f"{self.host}/api/generate", json=payload, timeout=self.timeout
            )
        except requests.exceptions.ConnectionError as e:
            raise LLMUnavailable(
                f"Can't reach Ollama at {self.host}. Is it running? "
                f"(`ollama serve`, then `ollama pull {self.model}`)"
            ) from e
        except requests.exceptions.Timeout as e:
            raise LLMUnavailable(
                f"Ollama at {self.host} didn't respond within {self.timeout}s."
            ) from e

        if resp.status_code == 404:
            raise LLMUnavailable(
                f"Model '{self.model}' isn't pulled. Run: ollama pull {self.model}"
            )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()


def get_llm_client() -> LLMClient:
    """Factory selecting the backend from config. Only 'ollama' exists
    today — the free, local, default path."""
    if config.LLM_BACKEND == "ollama":
        return OllamaClient()
    raise ValueError(
        f"Unknown SARVOS_LLM_BACKEND '{config.LLM_BACKEND}'. Only 'ollama' "
        "is implemented in this build."
    )
