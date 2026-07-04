"""
Memory Engine — implements the four memory types from the SARVOS spec.

Working memory   : in-process, current session only, never persisted.
Episodic memory   : full conversation history, persisted via Store.
Semantic memory   : retrievable facts/notes/preferences, indexed for search.
Procedural memory : named, reusable workflows.

Semantic retrieval note: the spec calls for "vector embeddings." Rather than
pull in a multi-GB transformer model for a foundation build, this uses
scikit-learn's TF-IDF as the retrieval backend behind an explicit interface
(`SemanticIndex`). It's a real, working nearest-neighbor-by-similarity search
today, and swapping in sentence-transformers or an API embedding model later
is a one-file change — nothing above this layer needs to know the difference.
That's the actual point of layering: Phase 1 should be honest about what it
uses, not fake a heavier stack it doesn't need yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from core.schemas import ConversationTurn, MemoryRecord
from memory.store import Store

_SUFFIXES = ("ences", "ence", "ing", "ies", "ied", "es", "ed", "s")


def _stem(word: str) -> str:
    """Minimal suffix-stripping stemmer — NOT a real Porter stemmer, just
    enough to close the most common gap found in manual testing: "prefer"
    vs "preferences" sharing zero tokens under plain TF-IDF. This is a
    stopgap; a real stemmer (nltk) or embeddings backend should replace it
    once dependencies beyond the stdlib/sklearn are acceptable."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _stemming_tokenizer(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return [_stem_to_fixed_point(w) for w in words]


def _stem_to_fixed_point(word: str) -> str:
    """Stem repeatedly until stable. sklearn re-tokenizes stop words with our
    tokenizer to validate consistency, which re-stems already-stemmed words
    (e.g. "across" -> "acros" -> "acro" on a second pass). Reaching a fixed
    point up front avoids that mismatch instead of just silencing it."""
    prev = word
    while True:
        current = _stem(prev)
        if current == prev:
            return current
        prev = current


def _stemmed_stop_words() -> list[str]:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    return sorted({_stem_to_fixed_point(w) for w in ENGLISH_STOP_WORDS})


class SemanticIndex:
    """Pluggable semantic search over MemoryRecords.

    Swap implementation later (e.g. sentence-transformers + a vector DB) by
    replacing `_vectorize` and `search` while keeping this same interface.
    """

    def __init__(self) -> None:
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None
        self._records: list[MemoryRecord] = []

    def rebuild(self, records: list[MemoryRecord]) -> None:
        self._records = records
        if not records:
            self._vectorizer = None
            self._matrix = None
            return
        self._vectorizer = TfidfVectorizer(
            tokenizer=_stemming_tokenizer,
            stop_words=_stemmed_stop_words(),
            token_pattern=None,
        )
        self._matrix = self._vectorizer.fit_transform([r.text for r in records])

    def search(self, query: str, top_k: int = 5) -> list[tuple[MemoryRecord, float]]:
        if not self._records or self._vectorizer is None:
            return []
        query_vec = self._vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self._matrix)[0]
        ranked = sorted(
            zip(self._records, scores), key=lambda pair: pair[1], reverse=True
        )
        return [(rec, float(score)) for rec, score in ranked[:top_k] if score > 0]


@dataclass
class WorkingMemory:
    """Current task/session state. Deliberately NOT persisted — it's scratch
    space for the active request, cleared or replaced each session."""

    active_goal: str | None = None
    scratch: dict = field(default_factory=dict)

    def reset(self) -> None:
        self.active_goal = None
        self.scratch = {}


class MemoryEngine:
    """Facade over all four memory types. Agents and the orchestrator talk
    to this, never to Store or SemanticIndex directly — keeps the storage
    and retrieval implementation swappable without touching agent code."""

    def __init__(self, store: Store | None = None):
        self.store = store or Store()
        self.working = WorkingMemory()
        self._index = SemanticIndex()
        self._index.rebuild(self.store.all_memory_records())

    # ---- Episodic -----------------------------------------------------

    def record_turn(self, turn: ConversationTurn) -> None:
        self.store.save_turn(turn)

    def recent_history(self, limit: int = 20) -> list[ConversationTurn]:
        return self.store.recent_turns(limit)

    # ---- Semantic -------------------------------------------------------

    def remember(
        self, text: str, kind: str = "note", tags: list[str] | None = None,
        source_turn_id: str | None = None,
    ) -> MemoryRecord:
        """Store a fact/preference/note and make it searchable immediately."""
        record = MemoryRecord(
            text=text, kind=kind, tags=tags or [], source_turn_id=source_turn_id
        )
        self.store.save_memory_record(record)
        self._index.rebuild(self.store.all_memory_records())
        return record

    def recall(self, query: str, top_k: int = 5) -> list[tuple[MemoryRecord, float]]:
        return self._index.search(query, top_k=top_k)

    def forget(self, record_id: str) -> bool:
        """User-controlled deletion. Rebuilds the index so a deleted memory
        can never resurface in a later recall — required for the spec's
        'user-controlled deletion' transparency guarantee."""
        deleted = self.store.delete_memory_record(record_id)
        if deleted:
            self._index.rebuild(self.store.all_memory_records())
        return deleted

    # ---- Procedural -------------------------------------------------------

    def save_workflow(self, name: str, description: str, steps: list[str]) -> None:
        self.store.save_workflow(name, description, steps)

    def get_workflow(self, name: str) -> dict | None:
        return self.store.get_workflow(name)
