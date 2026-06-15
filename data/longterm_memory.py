"""SQLite-backed long-term memory for Cerberus.

The database stores compact, typed memory records. It deliberately avoids raw
logs, raw snapshots, emails, OAuth payloads, secrets, and wallet keys.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from memory_system import DEFAULT_MEMORY_DIR, stable_hash, utc_now


DEFAULT_LONGTERM_DB = DEFAULT_MEMORY_DIR / "hellion.longterm.sqlite"


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS memory_items (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  text TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.5,
  importance INTEGER NOT NULL DEFAULT 50,
  hits INTEGER NOT NULL DEFAULT 1,
  contradictions INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  expires_at TEXT,
  source_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_items_identity
ON memory_items(kind, scope, key, source_hash);

CREATE INDEX IF NOT EXISTS idx_memory_items_lookup
ON memory_items(kind, scope, importance DESC, confidence DESC, last_seen DESC);
"""


FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
  id UNINDEXED,
  kind,
  scope,
  key,
  text
);
"""


def default_db_path() -> Path:
    root = os.getenv("CERBERUS_LONGTERM_DB")
    return Path(root) if root else DEFAULT_LONGTERM_DB


class LongTermMemoryStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> Path:
        with closing(self.connect()) as conn:
            conn.executescript(SCHEMA)
            try:
                conn.executescript(FTS_SCHEMA)
            except sqlite3.DatabaseError:
                pass
            conn.commit()
        return self.path

    def remember(
        self,
        *,
        kind: str,
        scope: str,
        key: str,
        text: str,
        confidence: float = 0.5,
        importance: int = 50,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.initialize()
        now = utc_now()
        clean_text = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())[:500]
        source_hash = stable_hash(
            {"kind": kind, "scope": scope, "key": key, "text": clean_text},
            length=24,
        )
        item_id = stable_hash({"kind": kind, "scope": scope, "key": key, "source_hash": source_hash}, length=32)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=True, separators=(",", ":"))
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO memory_items (
                  id, kind, scope, key, text, confidence, importance, first_seen,
                  last_seen, source_hash, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, scope, key, source_hash) DO UPDATE SET
                  hits = hits + 1,
                  confidence = max(confidence, excluded.confidence),
                  importance = max(importance, excluded.importance),
                  last_seen = excluded.last_seen
                """,
                (
                    item_id,
                    kind,
                    scope,
                    key,
                    clean_text,
                    max(0.0, min(1.0, confidence)),
                    max(0, min(100, importance)),
                    now,
                    now,
                    source_hash,
                    metadata_json,
                ),
            )
            try:
                conn.execute(
                    """
                    INSERT INTO memory_items_fts(id, kind, scope, key, text)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (item_id, kind, scope, key, clean_text),
                )
            except sqlite3.DatabaseError:
                pass
            conn.commit()
        return item_id

    def top(self, *, kind: str = "", scope: str = "", limit: int = 12) -> list[dict[str, Any]]:
        self.initialize()
        clauses = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with closing(self.connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT id, kind, scope, key, text, confidence, importance, hits, last_seen
                FROM memory_items
                {where}
                ORDER BY importance DESC, confidence DESC, hits DESC, last_seen DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def compact_context(self, *, kind: str = "", scope: str = "", limit: int = 12) -> str:
        return "\n".join(row["text"] for row in self.top(kind=kind, scope=scope, limit=limit))

    def stats(self) -> dict[str, Any]:
        self.initialize()
        with closing(self.connect()) as conn:
            count = conn.execute("SELECT count(*) FROM memory_items").fetchone()[0]
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        return {
            "path": str(self.path),
            "items": count,
            "bytes": page_count * page_size,
        }
