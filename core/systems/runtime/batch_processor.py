"""Cursor-based batch processor with checkpoint persistence.

Inspired by ClawHub's pattern of breaking long-running work into
resumable batches with cursor-based progress tracking. Each batch
processes a chunk of items, saves a cursor, and can be resumed
from the last checkpoint after failures or restarts.

Usage:
    store = BatchCheckpointStore("workspace/data/batch_checkpoints.db")
    processor = BatchProcessor(store)

    def process_fn(cursor, batch_size):
        items = fetch_items(after=cursor, limit=batch_size)
        for item in items:
            do_work(item)
        new_cursor = items[-1].id if items else cursor
        return BatchResult(
            cursor=new_cursor,
            processed=len(items),
            done=len(items) < batch_size,
        )

    result = processor.run("my_job", process_fn, batch_size=50)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    """Return value from a batch processing function."""

    cursor: str | None = None
    processed: int = 0
    done: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchCheckpoint:
    """Persisted state for a batch job."""

    job_name: str
    cursor: str | None = None
    total_processed: int = 0
    batch_count: int = 0
    started_at: float = 0.0
    updated_at: float = 0.0
    done_at: float | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_done(self) -> bool:
        return self.done_at is not None

    @property
    def elapsed_seconds(self) -> float:
        end = self.done_at or time.time()
        return end - self.started_at if self.started_at else 0.0


class BatchCheckpointStore:
    """SQLite-backed persistent storage for batch job checkpoints."""

    def __init__(self, db_path: str | Path = "workspace/data/batch_checkpoints.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS batch_checkpoints (
                    job_name TEXT PRIMARY KEY,
                    cursor TEXT,
                    total_processed INTEGER DEFAULT 0,
                    batch_count INTEGER DEFAULT 0,
                    started_at REAL,
                    updated_at REAL,
                    done_at REAL,
                    last_error TEXT,
                    metadata TEXT DEFAULT '{}'
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path), check_same_thread=False)

    def get(self, job_name: str) -> BatchCheckpoint | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM batch_checkpoints WHERE job_name = ?",
                (job_name,),
            ).fetchone()

        if row is None:
            return None

        return BatchCheckpoint(
            job_name=row[0],
            cursor=row[1],
            total_processed=row[2],
            batch_count=row[3],
            started_at=row[4] or 0.0,
            updated_at=row[5] or 0.0,
            done_at=row[6],
            last_error=row[7],
            metadata=json.loads(row[8]) if row[8] else {},
        )

    def save(self, checkpoint: BatchCheckpoint) -> None:
        checkpoint.updated_at = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO batch_checkpoints
                   (job_name, cursor, total_processed, batch_count,
                    started_at, updated_at, done_at, last_error, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    checkpoint.job_name,
                    checkpoint.cursor,
                    checkpoint.total_processed,
                    checkpoint.batch_count,
                    checkpoint.started_at,
                    checkpoint.updated_at,
                    checkpoint.done_at,
                    checkpoint.last_error,
                    json.dumps(checkpoint.metadata),
                ),
            )

    def reset(self, job_name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM batch_checkpoints WHERE job_name = ?", (job_name,))

    def list_jobs(self) -> list[BatchCheckpoint]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM batch_checkpoints ORDER BY updated_at DESC"
            ).fetchall()

        return [
            BatchCheckpoint(
                job_name=r[0], cursor=r[1], total_processed=r[2],
                batch_count=r[3], started_at=r[4] or 0.0,
                updated_at=r[5] or 0.0, done_at=r[6],
                last_error=r[7], metadata=json.loads(r[8]) if r[8] else {},
            )
            for r in rows
        ]

    def list_active(self) -> list[BatchCheckpoint]:
        return [cp for cp in self.list_jobs() if not cp.is_done]


BatchFn = Callable[[str | None, int], BatchResult]


class BatchProcessor:
    """Runs a batch function in resumable chunks with checkpoint persistence.

    The batch function signature: (cursor, batch_size) -> BatchResult
    It should process one batch of items starting from cursor and return
    the new cursor position, count processed, and whether it's done.
    """

    def __init__(self, store: BatchCheckpointStore):
        self.store = store

    def run(
        self,
        job_name: str,
        batch_fn: BatchFn,
        *,
        batch_size: int = 100,
        max_batches: int = 0,
        reset: bool = False,
    ) -> BatchCheckpoint:
        """Run batches until done or max_batches reached.

        Args:
            job_name: Unique identifier for this job
            batch_fn: Function(cursor, batch_size) -> BatchResult
            batch_size: Items per batch
            max_batches: Max batches per invocation (0 = unlimited)
            reset: Force restart from the beginning

        Returns:
            Final checkpoint state
        """
        if reset:
            self.store.reset(job_name)

        checkpoint = self.store.get(job_name)
        if checkpoint is None:
            checkpoint = BatchCheckpoint(
                job_name=job_name,
                started_at=time.time(),
            )
        elif checkpoint.is_done:
            checkpoint = BatchCheckpoint(
                job_name=job_name,
                started_at=time.time(),
            )

        batches_run = 0

        while True:
            if 0 < max_batches <= batches_run:
                logger.info(
                    "[BatchProcessor] %s: pausing after %d batches (max_batches=%d)",
                    job_name, batches_run, max_batches,
                )
                self.store.save(checkpoint)
                break

            try:
                result = batch_fn(checkpoint.cursor, batch_size)
                checkpoint.cursor = result.cursor
                checkpoint.total_processed += result.processed
                checkpoint.batch_count += 1
                checkpoint.last_error = None

                if result.metadata:
                    checkpoint.metadata.update(result.metadata)

                batches_run += 1

                if result.done:
                    checkpoint.done_at = time.time()
                    self.store.save(checkpoint)
                    logger.info(
                        "[BatchProcessor] %s: completed. %d items in %d batches (%.1fs)",
                        job_name, checkpoint.total_processed,
                        checkpoint.batch_count, checkpoint.elapsed_seconds,
                    )
                    break

                self.store.save(checkpoint)

                if result.processed == 0:
                    checkpoint.done_at = time.time()
                    self.store.save(checkpoint)
                    break

            except Exception as exc:
                checkpoint.last_error = str(exc)
                self.store.save(checkpoint)
                logger.error(
                    "[BatchProcessor] %s: error at batch %d (cursor=%s): %s",
                    job_name, checkpoint.batch_count, checkpoint.cursor, exc,
                )
                raise

        return checkpoint

    def resume(
        self,
        job_name: str,
        batch_fn: BatchFn,
        *,
        batch_size: int = 100,
        max_batches: int = 0,
    ) -> BatchCheckpoint | None:
        """Resume a previously started (and incomplete) job.

        Returns None if job is already done or doesn't exist.
        """
        checkpoint = self.store.get(job_name)
        if checkpoint is None or checkpoint.is_done:
            return None

        return self.run(
            job_name, batch_fn,
            batch_size=batch_size, max_batches=max_batches,
        )

    def get_progress(self, job_name: str) -> dict[str, Any]:
        """Get human-readable progress for a job."""
        cp = self.store.get(job_name)
        if cp is None:
            return {"job_name": job_name, "status": "not_started"}

        return {
            "job_name": cp.job_name,
            "status": "done" if cp.is_done else ("error" if cp.last_error else "running"),
            "cursor": cp.cursor,
            "total_processed": cp.total_processed,
            "batch_count": cp.batch_count,
            "elapsed_seconds": round(cp.elapsed_seconds, 1),
            "last_error": cp.last_error,
            "metadata": cp.metadata,
        }
