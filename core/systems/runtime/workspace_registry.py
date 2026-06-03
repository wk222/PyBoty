"""Registry of named workspace roots for the IDE / team console."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.systems.runtime.workspace_manager import WorkspaceManager

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class WorkspaceEntry:
    id: str
    name: str
    path: str
    is_default: bool = False
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkspaceRegistry:
    """Persisted list of workspace directories the IDE can switch between."""

    def __init__(self, registry_path: Path, default_workspace: Path):
        self._registry_path = registry_path
        self._default_workspace = default_workspace.resolve()
        self._entries: dict[str, WorkspaceEntry] = {}
        self._load()

    @classmethod
    def for_paths(cls, runtime_root: Path, default_workspace: Path) -> "WorkspaceRegistry":
        registry_path = runtime_root / ".runtime" / "workspaces.json"
        return cls(registry_path, default_workspace)

    def _load(self) -> None:
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        if self._registry_path.exists():
            try:
                raw = json.loads(self._registry_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        else:
            raw = {}

        entries_raw = raw.get("entries", []) if isinstance(raw, dict) else []
        self._entries = {}
        for item in entries_raw:
            if not isinstance(item, dict):
                continue
            entry = WorkspaceEntry(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "Workspace"),
                path=str(item.get("path") or ""),
                is_default=bool(item.get("is_default")),
                created_at=float(item.get("created_at") or 0.0),
            )
            if entry.id and entry.path:
                self._entries[entry.id] = entry

        if not any(entry.is_default for entry in self._entries.values()):
            default_id = "default"
            self._entries[default_id] = WorkspaceEntry(
                id=default_id,
                name="Default",
                path=str(self._default_workspace),
                is_default=True,
                created_at=time.time(),
            )
            self._save()

    def _save(self) -> None:
        payload = {
            "entries": [entry.to_dict() for entry in sorted(self._entries.values(), key=lambda e: e.name.lower())]
        }
        self._registry_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def list(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in sorted(self._entries.values(), key=lambda e: (not e.is_default, e.name.lower()))]

    def get(self, workspace_id: str | None) -> WorkspaceEntry | None:
        if not workspace_id:
            return self.default()
        return self._entries.get(workspace_id)

    def default(self) -> WorkspaceEntry:
        for entry in self._entries.values():
            if entry.is_default:
                return entry
        entry = WorkspaceEntry(
            id="default",
            name="Default",
            path=str(self._default_workspace),
            is_default=True,
            created_at=time.time(),
        )
        self._entries[entry.id] = entry
        self._save()
        return entry

    def resolve_path(self, workspace_id: str | None = None) -> Path:
        entry = self.get(workspace_id) or self.default()
        path = Path(entry.path).expanduser()
        if not path.is_absolute():
            path = (self._default_workspace.parent / path).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def manager(self, workspace_id: str | None = None) -> WorkspaceManager:
        return WorkspaceManager(str(self.resolve_path(workspace_id)))

    def create(self, name: str, *, path: str | None = None) -> WorkspaceEntry:
        clean_name = (name or "Workspace").strip() or "Workspace"
        slug = _SLUG_RE.sub("-", clean_name.lower()).strip("-") or "workspace"
        workspace_id = f"{slug}-{uuid.uuid4().hex[:6]}"

        if path:
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = (self._default_workspace.parent / target).resolve()
        else:
            root = self._default_workspace.parent / "workspaces" / slug
            target = root.resolve()

        target.mkdir(parents=True, exist_ok=True)
        mgr = WorkspaceManager(str(target))
        mgr.ensure_team_templates()

        entry = WorkspaceEntry(
            id=workspace_id,
            name=clean_name,
            path=str(target),
            is_default=False,
            created_at=time.time(),
        )
        self._entries[workspace_id] = entry
        self._save()
        return entry

    def delete(self, workspace_id: str) -> bool:
        entry = self._entries.get(workspace_id)
        if entry is None or entry.is_default:
            return False
        del self._entries[workspace_id]
        self._save()
        return True

    def thread_prefix(self, workspace_id: str | None = None) -> str:
        entry = self.get(workspace_id) or self.default()
        return f"ws:{entry.id}:"
