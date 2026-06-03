"""SqliteMemoryStore — single-table backend for the unified memory engine.

Why a single SQL table instead of N JSONL/Markdown files:

* Atomic writes / no race conditions across concurrent agents.
* Free FTS5 keyword recall + arbitrary metadata filter in one query.
* Embedding vectors stored alongside metadata, no separate vector DB.
* `gc()` / `reconsolidate()` / `feedback()` are O(log N) instead of
  re-parsing whole markdown files.

The schema is intentionally narrow (one mutable row per memory record)
so the SQL stays grep-able and human-debuggable. Each row carries:

  modality   : fact / episode / reflection / insight / journal / session_note
  scope      : session / agent / admin / global
  status     : active / forgotten / archived
  importance : static base (set at ingest time)
  importance_delta : adaptive learnt offset (recall + feedback)

Embeddings are stored as packed float32 BLOBs; cosine similarity is
computed in Python (numpy when available, pure-Python fallback) so we
never depend on chromadb / faiss for the memory layer.
"""

from __future__ import annotations

import array
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DB_FILENAME = "memory_engine.sqlite"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id                TEXT PRIMARY KEY,
    scope             TEXT NOT NULL,
    modality          TEXT NOT NULL,
    content           TEXT NOT NULL,
    metadata_json     TEXT,
    importance        REAL DEFAULT 0.5,
    importance_delta  REAL DEFAULT 0.0,
    recall_count      INTEGER DEFAULT 0,
    last_recall_ts    REAL DEFAULT 0,
    last_feedback_ts  REAL DEFAULT 0,
    first_seen_ts     REAL DEFAULT 0,
    status            TEXT DEFAULT 'active',
    embedding         BLOB,
    ts_event          REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_scope_modality ON memories(scope, modality, status);
CREATE INDEX IF NOT EXISTS idx_active_imp ON memories(status, importance DESC);
CREATE INDEX IF NOT EXISTS idx_modality_ts ON memories(modality, ts_event DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 1'
);

CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TABLE IF NOT EXISTS pipeline_state (
    name          TEXT PRIMARY KEY,
    last_run_ts   REAL,
    payload_json  TEXT
);

CREATE TABLE IF NOT EXISTS journal_dedup (
    content_hash  TEXT PRIMARY KEY,
    created_ts    REAL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS memory_links (
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    relation      TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_links_src ON memory_links(source_id);
CREATE INDEX IF NOT EXISTS idx_links_tgt ON memory_links(target_id);
"""


def _hash_record(scope: str, modality: str, content: str) -> str:
    # Compute normalized content signature to prevent near-duplicate storage leaks
    normalized = re.sub(r"[^\w\s\u4e00-\u9fff]", "", content.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized)
    raw = f"{scope}|{modality}|{normalized}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _pack_vector(vec: list[float]) -> bytes:
    return array.array("f", [float(v) for v in vec]).tobytes()


def _unpack_vector(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


@dataclass
class StoredRecord:
    """Raw row mapped from the ``memories`` table."""

    id: str
    scope: str
    modality: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    importance_delta: float = 0.0
    recall_count: int = 0
    last_recall_ts: float = 0.0
    last_feedback_ts: float = 0.0
    first_seen_ts: float = 0.0
    status: str = "active"
    ts_event: float = 0.0

    @property
    def effective_importance(self) -> float:
        return max(0.0, min(1.5, self.importance + self.importance_delta))


def _row_to_record(row: sqlite3.Row) -> StoredRecord:
    raw_meta = row["metadata_json"]
    meta: dict[str, Any] = {}
    if raw_meta:
        try:
            parsed = json.loads(raw_meta)
            if isinstance(parsed, dict):
                meta = parsed
        except json.JSONDecodeError:
            meta = {}
    return StoredRecord(
        id=row["id"],
        scope=row["scope"],
        modality=row["modality"],
        content=row["content"],
        metadata=meta,
        importance=float(row["importance"] or 0.5),
        importance_delta=float(row["importance_delta"] or 0.0),
        recall_count=int(row["recall_count"] or 0),
        last_recall_ts=float(row["last_recall_ts"] or 0.0),
        last_feedback_ts=float(row["last_feedback_ts"] or 0.0),
        first_seen_ts=float(row["first_seen_ts"] or 0.0),
        status=str(row["status"] or "active"),
        ts_event=float(row["ts_event"] or 0.0),
    )


class SQLiteFileLock:
    """Cross-process exclusive lock using a sidecar lock file for database safety."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fd = None

    def __enter__(self) -> "SQLiteFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    def acquire(self) -> None:
        try:
            if os.name == "nt":
                while True:
                    try:
                        self._fd = os.open(
                            str(self.lock_path),
                            os.O_CREAT | os.O_EXCL | os.O_WRONLY
                        )
                        break
                    except FileExistsError:
                        time.sleep(0.01)
            else:
                import fcntl
                self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_WRONLY)
                fcntl.flock(self._fd, fcntl.LOCK_EX)
        except Exception as exc:
            logger.debug("Failed to acquire process lock: %s", exc)

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
                if os.name == "nt":
                    try:
                        os.remove(str(self.lock_path))
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("Failed to release process lock: %s", exc)
            finally:
                self._fd = None


class SqliteMemoryStore:
    """Thread-safe and Process-safe SQLite backend for :class:`MemoryEngine`."""

    def __init__(self, workspace_dir: str | Path) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.db_path = self.workspace_dir / "memory" / DB_FILENAME
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.db_path.with_suffix(".lock")
        self._lock = threading.RLock()
        with SQLiteFileLock(self.lock_path):
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; we manage transactions explicitly
            )
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.executescript(_SCHEMA_SQL)
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA synchronous=NORMAL;")

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def _build_where_clause(
        self,
        scope: Any | None,
        modality: Any | None,
        status: Any | None,
        base_clauses: list[str] | None = None,
    ) -> tuple[str, list[Any]]:
        clauses = list(base_clauses or [])
        params: list[Any] = []
        
        if scope is not None:
            if isinstance(scope, (list, tuple, set, frozenset)):
                scopes_list = [s.value if hasattr(s, "value") else str(s) for s in scope]
                if scopes_list:
                    placeholders = ",".join("?" for _ in scopes_list)
                    clauses.append(f"scope IN ({placeholders})")
                    params.extend(scopes_list)
            else:
                scope_str = scope.value if hasattr(scope, "value") else str(scope)
                clauses.append("scope=?")
                params.append(scope_str)
                
        if modality is not None:
            if isinstance(modality, (list, tuple, set, frozenset)):
                mod_list = [m.value if hasattr(m, "value") else str(m) for m in modality]
                if mod_list:
                    placeholders = ",".join("?" for _ in mod_list)
                    clauses.append(f"modality IN ({placeholders})")
                    params.extend(mod_list)
            else:
                mod_str = modality.value if hasattr(modality, "value") else str(modality)
                clauses.append("modality=?")
                params.append(mod_str)
                
        if status is not None:
            if isinstance(status, (list, tuple, set, frozenset)):
                stat_list = [s.value if hasattr(s, "value") else str(s) for s in status]
                if stat_list:
                    placeholders = ",".join("?" for _ in stat_list)
                    clauses.append(f"status IN ({placeholders})")
                    params.extend(stat_list)
            else:
                stat_str = status.value if hasattr(status, "value") else str(status)
                clauses.append("status=?")
                params.append(stat_str)
                
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def upsert(
        self,
        *,
        scope: str,
        modality: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
        ts_event: float | None = None,
        embedding: list[float] | None = None,
        record_id: str | None = None,
    ) -> tuple[str, bool]:
        """Insert or update a memory record.

        Returns ``(record_id, was_inserted)``. Idempotent on
        ``(scope, modality, content)``.
        """
        if not content or not content.strip():
            raise ValueError("content cannot be empty")
        rid = record_id or _hash_record(scope, modality, content)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False) if metadata else None
        now = time.time()
        ts = ts_event if ts_event is not None else now
        blob = _pack_vector(embedding) if embedding else None
        with self._lock:
            cur = self._conn.execute(
                "SELECT id FROM memories WHERE id = ?", (rid,)
            )
            existing = cur.fetchone()
            if existing is not None:
                self._conn.execute(
                    "UPDATE memories SET content=?, metadata_json=?, importance=?, "
                    "ts_event=?, embedding=COALESCE(?, embedding) WHERE id=?",
                    (content, meta_json, importance, ts, blob, rid),
                )
                return rid, False
            self._conn.execute(
                "INSERT INTO memories (id, scope, modality, content, metadata_json,"
                " importance, importance_delta, recall_count, last_recall_ts,"
                " last_feedback_ts, first_seen_ts, status, embedding, ts_event)"
                " VALUES (?, ?, ?, ?, ?, ?, 0.0, 0, 0.0, 0.0, ?, 'active', ?, ?)",
                (rid, scope, modality, content, meta_json, importance, now, blob, ts),
            )
            return rid, True

    def update_status(self, record_id: str, status: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memories SET status=? WHERE id=?", (status, record_id)
            )
            return cur.rowcount > 0

    def delete(self, record_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id=?", (record_id,))
            return cur.rowcount > 0

    def delete_many(self, record_ids: Iterable[str]) -> int:
        ids = list(record_ids)
        if not ids:
            return 0
        with self._lock:
            placeholders = ",".join("?" * len(ids))
            cur = self._conn.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders})", ids
            )
            return cur.rowcount

    def touch_recall(
        self,
        record_id: str,
        *,
        ts: float | None = None,
        recall_bonus: float = 0.0,
    ) -> None:
        clock = ts if ts is not None else time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET recall_count = recall_count + 1,"
                " last_recall_ts = ?,"
                " importance_delta = MAX(-1.0, MIN(1.0, importance_delta + ?))"
                " WHERE id=?",
                (clock, recall_bonus, record_id),
            )

    def apply_feedback(
        self,
        record_id: str,
        *,
        delta: float,
        ts: float | None = None,
    ) -> bool:
        clock = ts if ts is not None else time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memories SET importance_delta = MAX(-1.0, MIN(1.0, importance_delta + ?)),"
                " last_feedback_ts = ? WHERE id=?",
                (delta, clock, record_id),
            )
            return cur.rowcount > 0

    def set_embedding(self, record_id: str, vec: list[float]) -> bool:
        blob = _pack_vector(vec)
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memories SET embedding=? WHERE id=?", (blob, record_id)
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def get(self, record_id: str) -> StoredRecord | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM memories WHERE id=?", (record_id,))
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def get_embedding(self, record_id: str) -> list[float] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT embedding FROM memories WHERE id=?", (record_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return _unpack_vector(row["embedding"])

    def list(
        self,
        *,
        scope: Any | None = None,
        modality: Any | None = None,
        status: Any | None = "active",
        order_by: str = "first_seen_ts DESC",
        limit: int = 200,
    ) -> list[StoredRecord]:
        where, params = self._build_where_clause(scope, modality, status)
        sql = f"SELECT * FROM memories{where} ORDER BY {order_by} LIMIT ?"
        params.append(int(limit))
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [_row_to_record(r) for r in rows]

    def count(
        self,
        *,
        scope: Any | None = None,
        modality: Any | None = None,
        status: Any | None = "active",
    ) -> int:
        where, params = self._build_where_clause(scope, modality, status)
        sql = f"SELECT COUNT(*) AS n FROM memories{where}"
        with self._lock:
            cur = self._conn.execute(sql, params)
            row = cur.fetchone()
        return int(row["n"] if row else 0)

    def search_fts(
        self,
        query: str,
        *,
        scope: Any | None = None,
        modality: Any | None = None,
        status: Any | None = "active",
        limit: int = 50,
    ) -> list[StoredRecord]:
        """Keyword recall via FTS5. Returns rows ordered by FTS rank."""
        if not query or not query.strip():
            return []
        match = json.dumps(query.strip())
        base_clauses = [f"memories.rowid IN (SELECT rowid FROM memories_fts WHERE content MATCH {match})"]
        where, params = self._build_where_clause(scope, modality, status, base_clauses=base_clauses)
        sql = (
            "SELECT memories.* FROM memories"
            f"{where}"
            " LIMIT ?"
        )
        params.append(int(limit))
        with self._lock:
            try:
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall()
            except sqlite3.OperationalError as exc:
                logger.debug("fts query failed (%s); falling back to LIKE", exc)
                rows = self._like_fallback(
                    query, scope=scope, modality=modality, status=status, limit=limit
                )
        return [_row_to_record(r) for r in rows]

    def _like_fallback(
        self,
        query: str,
        *,
        scope: Any | None,
        modality: Any | None,
        status: Any | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        base_clauses = ["content LIKE ?"]
        where, params = self._build_where_clause(scope, modality, status, base_clauses=base_clauses)
        params.insert(0, f"%{query.strip()}%")
        sql = f"SELECT * FROM memories{where} LIMIT ?"
        params.append(int(limit))
        cur = self._conn.execute(sql, params)
        return cur.fetchall()

    def search_embedding(
        self,
        query_vec: list[float],
        *,
        scope: Any | None = None,
        modality: Any | None = None,
        status: Any | None = "active",
        candidate_limit: int = 500,
    ) -> list[tuple[StoredRecord, float]]:
        """Brute-force cosine search over embeddings."""
        base_clauses = ["embedding IS NOT NULL"]
        where, params = self._build_where_clause(scope, modality, status, base_clauses=base_clauses)
        sql = (
            "SELECT *, embedding FROM memories"
            f"{where}"
            " ORDER BY (importance + importance_delta) DESC"
            " LIMIT ?"
        )
        params.append(int(candidate_limit))
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        scored: list[tuple[StoredRecord, float]] = []
        for row in rows:
            vec = _unpack_vector(row["embedding"])
            if not vec:
                continue
            sim = _cosine(query_vec, vec)
            scored.append((_row_to_record(row), sim))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def find_by_content(
        self,
        content: str,
        *,
        scope: str | None = None,
        modality: str | None = None,
    ) -> StoredRecord | None:
        if scope is None or modality is None:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT * FROM memories WHERE content=? LIMIT 1", (content,)
                )
                row = cur.fetchone()
            return _row_to_record(row) if row else None
        rid = _hash_record(scope, modality, content)
        return self.get(rid)

    # ------------------------------------------------------------------
    # Pipeline state (journal/distill cursor, dedup)
    # ------------------------------------------------------------------

    def get_pipeline_state(self, name: str) -> tuple[float, dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT last_run_ts, payload_json FROM pipeline_state WHERE name=?",
                (name,),
            )
            row = cur.fetchone()
        if row is None:
            return 0.0, {}
        last = float(row["last_run_ts"] or 0.0)
        try:
            payload = json.loads(row["payload_json"] or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except json.JSONDecodeError:
            payload = {}
        return last, payload

    def set_pipeline_state(
        self, name: str, *, last_run_ts: float, payload: dict[str, Any] | None = None
    ) -> None:
        body = json.dumps(payload or {}, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO pipeline_state(name, last_run_ts, payload_json)"
                " VALUES(?, ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET last_run_ts=excluded.last_run_ts,"
                " payload_json=excluded.payload_json",
                (name, last_run_ts, body),
            )

    def journal_seen(self, content_hash: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM journal_dedup WHERE content_hash=?", (content_hash,)
            )
            return cur.fetchone() is not None

    def journal_mark(self, content_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO journal_dedup(content_hash) VALUES(?)",
                (content_hash,),
            )

    # ------------------------------------------------------------------
    # Graph-Lite Relation Path Methods
    # ------------------------------------------------------------------

    def add_link(self, source_id: str, target_id: str, relation: str) -> None:
        """Insert or update an outbound relationship edge."""
        if not source_id or not target_id or not relation:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_links (source_id, target_id, relation)"
                " VALUES (?, ?, ?)",
                (source_id, target_id, relation.strip()),
            )

    def remove_link(self, source_id: str, target_id: str) -> None:
        """Delete an outbound relationship edge."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM memory_links WHERE source_id = ? AND target_id = ?",
                (source_id, target_id),
            )

    def get_links(self, source_id: str) -> list[dict[str, str]]:
        """Return all outbound links from this record."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT target_id, relation FROM memory_links WHERE source_id = ?",
                (source_id,),
            )
            rows = cur.fetchall()
        return [{"target_id": r["target_id"], "relation": r["relation"]} for r in rows]

    def get_associated_records(self, record_id: str, max_depth: int = 1) -> list[tuple[StoredRecord, str]]:
        """Bidirectional graph-traversal BFS returning (StoredRecord, relation_path) tuples."""
        results: list[tuple[StoredRecord, str]] = []
        if not record_id:
            return results
        with self._lock:
            visited = {record_id}
            queue: list[tuple[str, int, str]] = [(record_id, 0, "")]
            while queue:
                curr_id, depth, path = queue.pop(0)
                if depth >= max_depth:
                    continue
                # Outbound links
                cur_out = self._conn.execute(
                    "SELECT target_id, relation FROM memory_links WHERE source_id = ?", (curr_id,)
                )
                for row in cur_out.fetchall():
                    target_id = row["target_id"]
                    rel = row["relation"]
                    if target_id not in visited:
                        visited.add(target_id)
                        new_path = f"{path} -> [{rel}]" if path else rel
                        rec_row = self._conn.execute(
                            "SELECT * FROM memories WHERE id = ?", (target_id,)
                        ).fetchone()
                        if rec_row:
                            results.append((_row_to_record(rec_row), new_path))
                            queue.append((target_id, depth + 1, new_path))

                # Inbound links
                cur_in = self._conn.execute(
                    "SELECT source_id, relation FROM memory_links WHERE target_id = ?", (curr_id,)
                )
                for row in cur_in.fetchall():
                    source_id = row["source_id"]
                    rel = row["relation"]
                    if source_id not in visited:
                        visited.add(source_id)
                        new_path = f"{path} <- [{rel}]" if path else f"rev({rel})"
                        rec_row = self._conn.execute(
                            "SELECT * FROM memories WHERE id = ?", (source_id,)
                        ).fetchone()
                        if rec_row:
                            results.append((_row_to_record(rec_row), new_path))
                            queue.append((source_id, depth + 1, new_path))
            return results

    # ------------------------------------------------------------------
    # Maintenance / diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS total,"
                " SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active,"
                " SUM(CASE WHEN status='forgotten' THEN 1 ELSE 0 END) AS forgotten,"
                " SUM(CASE WHEN status='archived' THEN 1 ELSE 0 END) AS archived,"
                " SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS with_embedding"
                " FROM memories"
            ).fetchone()
        if not row:
            return {"total": 0, "active": 0, "forgotten": 0, "archived": 0, "with_embedding": 0}
        return {
            "total": int(row["total"] or 0),
            "active": int(row["active"] or 0),
            "forgotten": int(row["forgotten"] or 0),
            "archived": int(row["archived"] or 0),
            "with_embedding": int(row["with_embedding"] or 0),
        }

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")


__all__ = [
    "DB_FILENAME",
    "SqliteMemoryStore",
    "StoredRecord",
]
