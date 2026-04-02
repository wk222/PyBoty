"""In-process session kernel and sidechain state."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionSidechain:
    sidechain_id: str
    purpose: str
    status: str = "active"
    summary: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sidechain_id": self.sidechain_id,
            "purpose": self.purpose,
            "status": self.status,
            "summary": self.summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class SessionKernel:
    session_key: str
    artifact_version: int = 0
    last_compiled_at: float | None = None
    discovered_skill_names: list[str] = field(default_factory=list)
    loaded_memory_paths: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    file_view_projection: dict[str, Any] = field(default_factory=dict)
    mutable_artifacts: dict[str, Any] = field(default_factory=dict)
    invalidations: list[dict[str, Any]] = field(default_factory=list)
    sidechains: dict[str, SessionSidechain] = field(default_factory=dict)

    def invalidate(self, *, reason: str, scopes: list[str] | None = None) -> None:
        self.artifact_version += 1
        self.invalidations.append(
            {
                "timestamp": time.time(),
                "reason": str(reason).strip() or "update",
                "scopes": list(scopes or []),
                "artifact_version": self.artifact_version,
            }
        )
        if len(self.invalidations) > 32:
            self.invalidations = self.invalidations[-32:]

    def note_usage(self, counter_name: str, amount: int = 1) -> None:
        normalized = str(counter_name).strip()
        if not normalized:
            return
        self.usage[normalized] = int(self.usage.get(normalized, 0)) + max(1, int(amount))

    def note_skills(self, skill_names: list[str]) -> None:
        for item in skill_names:
            normalized = str(item).strip()
            if normalized and normalized not in self.discovered_skill_names:
                self.discovered_skill_names.append(normalized)

    def note_memory_paths(self, memory_paths: list[str]) -> None:
        for item in memory_paths:
            normalized = str(item).strip()
            if normalized and normalized not in self.loaded_memory_paths:
                self.loaded_memory_paths.append(normalized)

    def upsert_sidechain(
        self,
        *,
        purpose: str,
        summary: str = "",
        status: str = "active",
        metadata: dict[str, Any] | None = None,
        sidechain_id: str = "",
    ) -> SessionSidechain:
        normalized_id = str(sidechain_id).strip()
        if not normalized_id:
            normalized_id = f"sc-{uuid.uuid4().hex[:10]}"
        sidechain = self.sidechains.get(normalized_id)
        if sidechain is None:
            sidechain = SessionSidechain(
                sidechain_id=normalized_id,
                purpose=str(purpose).strip() or "background",
            )
            self.sidechains[normalized_id] = sidechain
        sidechain.status = str(status).strip() or sidechain.status
        if summary:
            sidechain.summary = str(summary).strip()
        if metadata:
            sidechain.metadata = {**sidechain.metadata, **dict(metadata)}
        sidechain.updated_at = time.time()
        return sidechain

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "artifact_version": self.artifact_version,
            "last_compiled_at": self.last_compiled_at,
            "discovered_skill_names": list(self.discovered_skill_names),
            "loaded_memory_paths": list(self.loaded_memory_paths),
            "usage": dict(self.usage),
            "file_view_projection": dict(self.file_view_projection),
            "mutable_artifacts": dict(self.mutable_artifacts),
            "invalidations": list(self.invalidations),
            "sidechains": [sidechain.to_dict() for sidechain in self.sidechains.values()],
        }
