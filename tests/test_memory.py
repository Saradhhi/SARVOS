import tempfile
from pathlib import Path

import pytest

from core.schemas import AgentName, ConversationTurn
from memory.engine import MemoryEngine
from memory.store import Store


@pytest.fixture
def memory() -> MemoryEngine:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        yield MemoryEngine(store=Store(db_path))


def test_episodic_roundtrip(memory: MemoryEngine):
    turn = ConversationTurn(request_id="r1", role="user", content="hello sarvos")
    memory.record_turn(turn)
    history = memory.recent_history(limit=5)
    assert len(history) == 1
    assert history[0].content == "hello sarvos"


def test_recent_history_preserves_order(memory: MemoryEngine):
    for i in range(3):
        memory.record_turn(
            ConversationTurn(request_id="r1", role="user", content=f"message {i}")
        )
    history = memory.recent_history(limit=10)
    assert [t.content for t in history] == ["message 0", "message 1", "message 2"]


def test_recent_history_preserves_order_even_with_identical_timestamps(memory: MemoryEngine):
    """Regression test for a real, platform-specific bug: the original
    ordering query used `ORDER BY timestamp DESC`, which relies on the
    timestamp STRING being unique per turn. Turns created in a tight loop
    with no delay got IDENTICAL timestamps on Windows (whose clock
    resolution is coarser than Linux's) -- something that never once
    reproduced in the Linux sandbox this project was built in, and only
    surfaced from a real test run on Windows. This test forces that exact
    tie scenario deterministically (identical timestamps on every turn),
    rather than depending on real clock timing/platform to happen to
    trigger it -- proving the fix (ordering by SQLite's insertion-order
    rowid instead) holds regardless of what the wall clock says."""
    from datetime import datetime, timezone

    same_instant = datetime.now(timezone.utc)
    for i in range(5):
        memory.record_turn(
            ConversationTurn(
                request_id="r1", role="user", content=f"tied message {i}",
                timestamp=same_instant,  # identical on every turn, deliberately
            )
        )
    history = memory.recent_history(limit=10)
    assert [t.content for t in history] == [
        "tied message 0", "tied message 1", "tied message 2",
        "tied message 3", "tied message 4",
    ]


def test_memory_records_preserve_insertion_order_even_with_identical_timestamps(memory: MemoryEngine):
    """Proactive test applying the same lesson as
    test_recent_history_preserves_order_even_with_identical_timestamps:
    all_memory_records() had the identical tie-breaking vulnerability
    (ordering by a timestamp string with no monotonic tiebreaker), fixed
    the same way (ordering by rowid instead)."""
    from datetime import datetime, timezone

    same_instant = datetime.now(timezone.utc)
    for i in range(4):
        record = memory.remember(f"tied fact {i}")
        # Force the exact tie scenario by overwriting created_at directly
        # in storage, bypassing remember()'s use of "now" for each call
        # (which, on a fast machine, could genuinely differ by a
        # microsecond or two even without the platform-specific issue).
        with memory.store._connect() as conn:
            conn.execute(
                "UPDATE memory_records SET created_at = ? WHERE record_id = ?",
                (same_instant.isoformat(), record.record_id),
            )

    records = memory.store.all_memory_records()
    assert [r.text for r in records] == [
        "tied fact 0", "tied fact 1", "tied fact 2", "tied fact 3",
    ]


def test_semantic_remember_and_recall(memory: MemoryEngine):
    memory.remember("I prefer dark mode in all applications")
    memory.remember("My favorite programming language is Python")
    results = memory.recall("dark mode preference")
    assert results, "expected at least one match"
    top_text = results[0][0].text
    assert "dark mode" in top_text


def test_tfidf_backend_is_lexical_not_semantic(memory: MemoryEngine):
    """Documents a known, real limitation: TF-IDF matches on shared words,
    not meaning. A query with zero vocabulary overlap won't find a relevant
    memory even though a human would consider it an obvious match. This is
    exactly the gap a future embedding-based SemanticIndex closes — the test
    exists so that swap is verifiably a behavior change, not just a
    refactor."""
    memory.remember("I prefer dark mode in all applications")
    results = memory.recall("what theme do I like")  # no shared words
    assert results == []


def test_forget_removes_from_recall(memory: MemoryEngine):
    record = memory.remember("I dislike loud notifications")
    assert memory.recall("notifications")
    ok = memory.forget(record.record_id)
    assert ok
    # After deletion the index is rebuilt without it.
    results = memory.recall("loud notifications")
    assert all(r.record_id != record.record_id for r, _ in results)


def test_forget_unknown_id_returns_false(memory: MemoryEngine):
    assert memory.forget("mem_does_not_exist") is False
