"""Persistence helpers for dynamic tool definitions."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ToolStorage:
    """Manage persisted tool definitions in memory and on disk."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir_path = Path(base_dir).resolve() if base_dir else None
        self.base_dir = str(self.base_dir_path) if self.base_dir_path else None
        self.tools: dict[str, dict[str, Any]] = {}

        if self.base_dir_path is not None:
            self._ensure_base_dir()
            self.reload()

    def _ensure_base_dir(self) -> None:
        """Create the base directory if this storage is disk-backed."""
        if self.base_dir_path is not None:
            self.base_dir_path.mkdir(parents=True, exist_ok=True)

    def _tool_path(self, name: str) -> Path:
        if self.base_dir_path is None:
            raise ValueError("Storage is not backed by a filesystem directory")
        return self.base_dir_path / f"{name}.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected object JSON in {path}")
        return data

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def reload(self) -> None:
        """Reload persisted tool definitions from disk."""
        if self.base_dir_path is None or not self.base_dir_path.exists():
            return

        self.tools = {}
        for path in sorted(self.base_dir_path.glob("*.json")):
            try:
                data = self._read_json(path)
            except Exception as exc:
                print(f"Error loading tool from {path}: {exc}")
                continue

            name = data.get("name")
            if name:
                self.tools[str(name)] = data

    def _save_tool(self, name: str, definition: dict[str, Any]) -> None:
        """Persist a single tool definition when disk backing is enabled."""
        if self.base_dir_path is None:
            return
        self._write_json(self._tool_path(name), definition)

    def add_tool(self, name: str, definition: dict[str, Any]) -> bool:
        """Add a tool only when it does not already exist."""
        if name in self.tools:
            return False

        self.tools[name] = definition
        self._save_tool(name, definition)
        return True

    def upsert_tool(self, name: str, definition: dict[str, Any]) -> bool:
        """Insert or replace a tool definition."""
        self.tools[name] = definition
        self._save_tool(name, definition)
        return True

    def get_tool(self, name: str) -> dict[str, Any] | None:
        """Return a single persisted tool definition."""
        return self.tools.get(name)

    def remove_tool(self, name: str) -> bool:
        """Delete a tool from memory and disk."""
        if name not in self.tools:
            return False

        del self.tools[name]
        if self.base_dir_path is not None:
            path = self._tool_path(name)
            if path.exists():
                try:
                    path.unlink()
                except Exception as exc:
                    print(f"Error removing tool file {path}: {exc}")
        return True

    def get_failed_tools(self) -> list[str]:
        """Placeholder for future failure tracking."""
        return []

    def list_tools(self) -> dict[str, str]:
        """Return tool names mapped to descriptions."""
        return {name: definition.get("description", "") for name, definition in self.tools.items()}

    def to_dict(self) -> dict[str, Any]:
        """Serialize this storage snapshot."""
        return {
            "tools": self.tools,
            "count": len(self.tools),
            "timestamp": time.time(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolStorage:
        """Restore an in-memory storage snapshot."""
        storage = cls()
        storage.tools = dict(data.get("tools", {}))
        return storage

    def export_to_json(self, filepath: str | Path) -> None:
        """Export the current tool snapshot to a JSON file."""
        Path(filepath).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def import_from_json(cls, filepath: str | Path) -> ToolStorage:
        """Import a tool snapshot from JSON."""
        data = json.loads(Path(filepath).read_text(encoding="utf-8"))
        return cls.from_dict(data)


@dataclass
class ToolContext:
    """Persisted context snapshot for tool usage and preferences."""

    tool_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_usage: dict[str, int] = field(default_factory=dict)
    user_preferences: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_definitions": self.tool_definitions,
            "tool_usage": self.tool_usage,
            "user_preferences": self.user_preferences,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolContext:
        if not isinstance(data, dict):
            return cls()

        return cls(
            tool_definitions=data.get("tool_definitions", {}),
            tool_usage=data.get("tool_usage", {}),
            user_preferences=data.get("user_preferences", {}),
        )

    def increment_usage(self, tool_name: str) -> None:
        self.tool_usage[tool_name] = self.tool_usage.get(tool_name, 0) + 1
