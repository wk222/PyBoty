"""Persistent checkpointer helpers for subagent runtimes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from core.systems.runtime.project_paths import ProjectPaths


@dataclass
class SubagentCheckpointBundle:
    """Resolved subagent checkpointer and its optional sqlite connection."""

    checkpointer: Any
    backend: str
    path: Path | None = None
    connection: sqlite3.Connection | None = None

    def close(self) -> None:
        if self.connection is None:
            return
        try:
            self.connection.close()
        except Exception:
            pass
        finally:
            self.connection = None


def build_subagent_checkpointer(
    *,
    agent_name: str,
    project_paths: ProjectPaths | None = None,
) -> SubagentCheckpointBundle:
    """Create a persistent sqlite checkpointer when project paths are available."""
    if project_paths is None:
        return SubagentCheckpointBundle(checkpointer=MemorySaver(), backend="memory")

    checkpoints_dir = (project_paths.tools_workspace_dir / "subagents" / agent_name).resolve()
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoints_dir / "checkpoints.sqlite"
    connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    return SubagentCheckpointBundle(
        checkpointer=SqliteSaver(connection),
        backend="sqlite",
        path=checkpoint_path,
        connection=connection,
    )
