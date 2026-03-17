"""Tool result cache with TTL and read-write locking.

Avoids redundant tool executions when the same arguments are used
within a configurable time window.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class _RWLock:
    """Simple readers-writer lock: multiple concurrent reads, exclusive writes."""

    def __init__(self):
        self._read_ready = threading.Condition(threading.Lock())
        self._readers = 0

    def acquire_read(self):
        with self._read_ready:
            self._readers += 1

    def release_read(self):
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self):
        self._read_ready.acquire()
        while self._readers > 0:
            self._read_ready.wait()

    def release_write(self):
        self._read_ready.release()


@dataclass
class _CacheEntry:
    result: Any
    created_at: float
    ttl: float | None


class ToolCache:
    """Thread-safe cache for tool call results."""

    def __init__(self, default_ttl: float | None = 300.0):
        self._lock = _RWLock()
        self._store: dict[str, _CacheEntry] = {}
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    @staticmethod
    def hash_args(args: dict[str, Any]) -> str:
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def _cache_key(self, tool_name: str, args_hash: str) -> str:
        return f"{tool_name}::{args_hash}"

    def get(self, tool_name: str, args_hash: str) -> tuple[bool, Any]:
        """Return (hit, value). If hit is False, value is None."""
        key = self._cache_key(tool_name, args_hash)
        self._lock.acquire_read()
        try:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return False, None
            if entry.ttl is not None and time.time() - entry.created_at > entry.ttl:
                self._misses += 1
                return False, None
            self._hits += 1
            return True, entry.result
        finally:
            self._lock.release_read()

    def set(self, tool_name: str, args_hash: str, result: Any, *, ttl: float | None = ...) -> None:  # type: ignore[assignment]
        """Store a result. Use ttl=None for no expiry, omit for default."""
        effective_ttl = self._default_ttl if ttl is ... else ttl
        key = self._cache_key(tool_name, args_hash)
        self._lock.acquire_write()
        try:
            self._store[key] = _CacheEntry(result=result, created_at=time.time(), ttl=effective_ttl)
        finally:
            self._lock.release_write()

    def invalidate(self, tool_name: str | None = None) -> int:
        """Clear cache entries. If tool_name given, only that tool. Returns count removed."""
        self._lock.acquire_write()
        try:
            if tool_name is None:
                count = len(self._store)
                self._store.clear()
                return count
            prefix = f"{tool_name}::"
            keys_to_remove = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._store[k]
            return len(keys_to_remove)
        finally:
            self._lock.release_write()

    def stats(self) -> dict[str, Any]:
        self._lock.acquire_read()
        try:
            total = self._hits + self._misses
            return {
                "size": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / total if total > 0 else 0.0,
            }
        finally:
            self._lock.release_read()

    def _evict_expired(self) -> int:
        """Remove expired entries. Returns count evicted."""
        now = time.time()
        self._lock.acquire_write()
        try:
            expired = [k for k, e in self._store.items() if e.ttl is not None and now - e.created_at > e.ttl]
            for k in expired:
                del self._store[k]
            return len(expired)
        finally:
            self._lock.release_write()


def cached_tool_call(
    cache: ToolCache,
    tool_name: str,
    args: dict[str, Any],
    fn: Any,
    *,
    cacheable: bool = True,
    ttl: float | None = ...,  # type: ignore[assignment]
) -> Any:
    """Execute a tool with caching. If cacheable and cached, return cached result."""
    if not cacheable:
        return fn(**args)

    args_hash = cache.hash_args(args)
    hit, value = cache.get(tool_name, args_hash)
    if hit:
        logger.debug("Cache HIT for %s(%s)", tool_name, args_hash[:8])
        return value

    logger.debug("Cache MISS for %s(%s)", tool_name, args_hash[:8])
    result = fn(**args)
    cache.set(tool_name, args_hash, result, ttl=ttl)
    return result
