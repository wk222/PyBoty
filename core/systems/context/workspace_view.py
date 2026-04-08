"""Shared workspace-view state for file-oriented runtime capabilities."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkspaceViewEntry:
    content_hash: str
    mtime: float
    file_size: int
    line_count: int
    offset: int = 0
    limit: int = 0
    is_partial: bool = False
    total_lines: int = 0

    @property
    def range_key(self) -> tuple[int, int]:
        return (self.offset, self.limit)


class WorkspaceViewService:
    """Tracks file views shared across read/write-oriented tools.

    This is the trunk-level "workspace view" abstraction:
    - full-file reads can be deduplicated across turns
    - exact partial-range reads can also be deduplicated
    - writes invalidate the cached view state for the file
    """

    def __init__(self) -> None:
        self._full_views: dict[str, WorkspaceViewEntry] = {}
        self._partial_views: dict[str, dict[tuple[int, int], WorkspaceViewEntry]] = {}
        self._recent_views: list[dict[str, Any]] = []
        self._stats = {
            "hits": 0,
            "misses": 0,
            "invalidations": 0,
            "full_hits": 0,
            "partial_hits": 0,
            "full_records": 0,
            "partial_records": 0,
        }

    def record_view(
        self,
        *,
        resolved_path: str,
        content: str,
        mtime: float,
        file_size: int,
        offset: int = 0,
        limit: int = 0,
        is_partial: bool = False,
        total_lines: int = 0,
    ) -> WorkspaceViewEntry:
        entry = WorkspaceViewEntry(
            content_hash=hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest(),
            mtime=mtime,
            file_size=file_size,
            line_count=len(content.splitlines()) if content else 0,
            offset=offset,
            limit=limit,
            is_partial=is_partial,
            total_lines=total_lines,
        )
        if is_partial:
            bucket = self._partial_views.setdefault(resolved_path, {})
            bucket[entry.range_key] = entry
            self._stats["partial_records"] += 1
        else:
            self._full_views[resolved_path] = entry
            self._stats["full_records"] += 1
        self._record_recent_view(resolved_path=resolved_path, entry=entry, content=content)
        return entry

    def get_cached_view(
        self,
        resolved_path: str,
        current_mtime: float,
        current_size: int,
        *,
        offset: int = 0,
        limit: int = 0,
    ) -> WorkspaceViewEntry | None:
        if offset == 0 and limit == 0:
            entry = self._full_views.get(resolved_path)
            if entry is None:
                self._stats["misses"] += 1
                return None
            if not self._is_current(entry, current_mtime, current_size):
                self._stats["misses"] += 1
                self._full_views.pop(resolved_path, None)
                return None
            self._stats["hits"] += 1
            self._stats["full_hits"] += 1
            return entry

        bucket = self._partial_views.get(resolved_path)
        if not bucket:
            self._stats["misses"] += 1
            return None

        entry = bucket.get((offset, limit))
        if entry is None:
            self._stats["misses"] += 1
            return None
        if not self._is_current(entry, current_mtime, current_size):
            self._stats["misses"] += 1
            bucket.pop((offset, limit), None)
            if not bucket:
                self._partial_views.pop(resolved_path, None)
            return None

        self._stats["hits"] += 1
        self._stats["partial_hits"] += 1
        return entry

    def invalidate(self, resolved_path: str) -> None:
        removed = False
        if self._full_views.pop(resolved_path, None) is not None:
            removed = True
        if self._partial_views.pop(resolved_path, None) is not None:
            removed = True
        if removed:
            self._stats["invalidations"] += 1
            logger.debug("WorkspaceViewService: invalidated %s", resolved_path)

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def __len__(self) -> int:
        return len(self._full_views) + sum(len(bucket) for bucket in self._partial_views.values())

    def build_projection(self, *, limit: int = 8) -> dict[str, Any]:
        capped_limit = max(1, int(limit or 0))
        recent_views = list(self._recent_views[-capped_limit:])
        recent_paths: list[str] = []
        for item in recent_views:
            path = str(item.get("path", "")).strip()
            if path and path not in recent_paths:
                recent_paths.append(path)
        return {
            "recent_paths": recent_paths,
            "recent_views": recent_views,
            "notebook_summary": "",
            "partial_views": sum(1 for item in recent_views if bool(item.get("is_partial_view"))),
            "path_labels": [Path(path).name for path in recent_paths],
            "view_hashes": [
                str(item.get("view_hash", "")).strip()
                for item in recent_views
                if str(item.get("view_hash", "")).strip()
            ],
            "stats": self.stats,
        }

    @staticmethod
    def _is_current(entry: WorkspaceViewEntry, current_mtime: float, current_size: int) -> bool:
        return entry.mtime == current_mtime and entry.file_size == current_size

    def _record_recent_view(
        self,
        *,
        resolved_path: str,
        entry: WorkspaceViewEntry,
        content: str,
    ) -> None:
        total_lines = entry.total_lines or entry.line_count
        start_line = entry.offset + 1 if entry.is_partial else 1
        if entry.is_partial:
            end_line = min(entry.offset + entry.limit, total_lines) if entry.limit > 0 else total_lines
        else:
            end_line = total_lines
        preview = ""
        stripped = content.strip()
        if stripped:
            preview = stripped[:160]
        view_hash = hashlib.md5(
            f"{resolved_path}:{entry.content_hash}:{entry.offset}:{entry.limit}:{int(entry.is_partial)}".encode(
                "utf-8",
                errors="replace",
            )
        ).hexdigest()
        self._recent_views.append(
            {
                "path": resolved_path,
                "offset": entry.offset,
                "limit": entry.limit,
                "is_partial_view": entry.is_partial,
                "view_kind": "partial" if entry.is_partial else "full",
                "line_count": entry.line_count,
                "total_lines": total_lines,
                "line_range": f"{start_line}-{end_line}/{total_lines}",
                "file_size": entry.file_size,
                "content_hash": entry.content_hash,
                "view_hash": view_hash,
                "preview": preview,
            }
        )
        self._recent_views = self._recent_views[-24:]
