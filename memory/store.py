"""
Durable storage layer. SQLite, local-first, single file on disk.

This backs Episodic Memory (conversation history) and Procedural Memory
(reusable workflows). Semantic Memory's *index* lives in engine.py, but its
underlying records are also persisted here — the index is rebuilt from
these rows, so it's always reconstructible and never a source of truth.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.schemas import ConversationTurn, MemoryRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    agent TEXT,
    timestamp TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS memory_records (
    record_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'note',
    source_turn_id TEXT,
    created_at TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workflows (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    steps TEXT NOT NULL,  -- JSON list of step instructions
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS bookmarks (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Snapshots of page text, so "what changed since last time" is answered by
-- comparing against something real rather than by asking a model to recall.
CREATE TABLE IF NOT EXISTS page_snapshots (
    url TEXT PRIMARY KEY,
    text_hash TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    task_id TEXT,
    agent TEXT,
    risk TEXT,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


class Store:
    """Thin, explicit wrapper around SQLite. No ORM — the schema is small
    enough that a query layer would add indirection without real benefit."""

    def __init__(self, db_path: str | Path = "sarvos.db"):
        self.db_path = str(db_path)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- Episodic memory --------------------------------------------------

    def save_turn(self, turn: ConversationTurn) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO turns (turn_id, request_id, role, content, agent, "
                "timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    turn.turn_id,
                    turn.request_id,
                    turn.role,
                    turn.content,
                    turn.agent.value if turn.agent else None,
                    turn.timestamp.isoformat(),
                    json.dumps(turn.metadata),
                ),
            )

    def recent_turns(self, limit: int = 20) -> list[ConversationTurn]:
        with self._connect() as conn:
            rows = conn.execute(
                # Ordered by SQLite's implicit rowid, NOT the timestamp
                # column. rowid strictly increases with insertion order
                # and is completely immune to clock resolution -- ordering
                # by timestamp instead caused a real, platform-specific
                # bug: turns created in a tight loop with no delay got
                # IDENTICAL timestamp strings on Windows (whose clock
                # resolution is coarser than Linux's), and SQLite's
                # tie-breaking for equal values isn't guaranteed to match
                # insertion order -- confirmed by a real test failure that
                # never once reproduced in the Linux sandbox this project
                # was built in. audit_log already avoided this by using a
                # real INTEGER PRIMARY KEY AUTOINCREMENT and ordering by
                # that; this applies the same fix here via rowid, since
                # turn_id is a TEXT primary key (SQLite still maintains an
                # implicit auto-incrementing rowid alongside it).
                "SELECT * FROM turns ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        turns = [_row_to_turn(r) for r in rows]
        return list(reversed(turns))

    # ---- Semantic memory (records) ----------------------------------------

    def save_memory_record(self, record: MemoryRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO memory_records (record_id, text, kind, "
                "source_turn_id, created_at, tags) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.record_id,
                    record.text,
                    record.kind,
                    record.source_turn_id,
                    record.created_at.isoformat(),
                    json.dumps(record.tags),
                ),
            )

    def all_memory_records(self) -> list[MemoryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_records WHERE deleted = 0 "
                # Ordered by rowid (insertion order), not the created_at
                # timestamp string -- same fix as recent_turns() above,
                # applied here proactively since this has the identical
                # tie-breaking vulnerability (multiple records created in
                # rapid succession can get identical timestamps on
                # platforms with coarser clock resolution, e.g. Windows
                # vs. Linux).
                "ORDER BY rowid ASC"
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def delete_memory_record(self, record_id: str) -> bool:
        """Soft-delete. User-controlled deletion per the spec's memory
        transparency requirement — nothing is silently purged, but it's
        excluded from retrieval and future indexing."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE memory_records SET deleted = 1 WHERE record_id = ?",
                (record_id,),
            )
        return cur.rowcount > 0

    # ---- Procedural memory (workflows) -------------------------------------

    def save_workflow(self, name: str, description: str, steps: list[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO workflows (name, description, steps, created_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(name) DO UPDATE SET description=excluded.description, "
                "steps=excluded.steps",
                (name, description, json.dumps(steps)),
            )

    def get_workflow(self, name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflows WHERE name = ?", (name,)
            ).fetchone()
        if not row:
            return None
        return {
            "name": row["name"],
            "description": row["description"],
            "steps": json.loads(row["steps"]),
        }

    # ---- Audit log ----------------------------------------------------------

    # ---- bookmarks -------------------------------------------------------

    def save_bookmark(self, name: str, url: str, title: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bookmarks (name, url, title, created_at) "
                "VALUES (?, ?, ?, ?)",
                (name, url, title, datetime.now(timezone.utc).isoformat()),
            )

    def all_bookmarks(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, url, title, created_at FROM bookmarks ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_bookmark(self, name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, url, title FROM bookmarks WHERE name = ?", (name,)
            ).fetchone()
        return dict(row) if row else None

    def delete_bookmark(self, name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM bookmarks WHERE name = ?", (name,))
        return cur.rowcount > 0

    # ---- page snapshots --------------------------------------------------

    def save_page_snapshot(self, url: str, text_hash: str, char_count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO page_snapshots (url, text_hash, char_count, captured_at) "
                "VALUES (?, ?, ?, ?)",
                (url, text_hash, char_count, datetime.now(timezone.utc).isoformat()),
            )

    def get_page_snapshot(self, url: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT url, text_hash, char_count, captured_at FROM page_snapshots WHERE url = ?",
                (url,),
            ).fetchone()
        return dict(row) if row else None

    def log_action(
        self, action: str, task_id: str = "", agent: str = "", risk: str = "", detail: str = ""
    ) -> None:
        """Every agent invocation and every confirmation decision gets logged
        here — this is what makes SARVOS's actions observable, per the spec's
        'no complex task should skip planning ... every action observable'
        principle. It's append-only; nothing here is ever deleted."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (timestamp, task_id, agent, risk, action, detail) "
                "VALUES (datetime('now'), ?, ?, ?, ?, ?)",
                (task_id, agent, risk, action, detail),
            )

    def recent_audit_log(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def _row_to_turn(row: sqlite3.Row) -> ConversationTurn:
    from datetime import datetime as _dt

    return ConversationTurn(
        turn_id=row["turn_id"],
        request_id=row["request_id"],
        role=row["role"],
        content=row["content"],
        agent=row["agent"],
        timestamp=_dt.fromisoformat(row["timestamp"]),
        metadata=json.loads(row["metadata"]),
    )


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    from datetime import datetime as _dt

    return MemoryRecord(
        record_id=row["record_id"],
        text=row["text"],
        kind=row["kind"],
        source_turn_id=row["source_turn_id"],
        created_at=_dt.fromisoformat(row["created_at"]),
        tags=json.loads(row["tags"]),
    )
