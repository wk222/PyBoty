"""
Project path helpers.

Centralizes source/runtime boundaries so entrypoints do not scatter path logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ProjectPaths:
    root_dir: Path
    workspace_dir: Path
    global_tools_dir: Path
    agents_dir: Path
    uv_envs_dir: Path
    checkpoints_db: Path
    approvals_file: Path
    conversations_file: Path
    chat_history_dir: Path
    conversation_offload_dir: Path
    workspace_data_dir: Path
    workspace_evicted_dir: Path
    apps_dir: Path
    uploads_dir: Path
    skills_dir: Path
    workflows_dir: Path
    tools_workspace_dir: Path

    @classmethod
    def from_root(
        cls,
        root_dir: str | Path | None = None,
        workspace_dir: str | Path | None = None,
    ) -> ProjectPaths:
        root = Path(root_dir) if root_dir else _PROJECT_ROOT
        root = root.resolve()

        workspace = Path(workspace_dir) if workspace_dir else root / "workspace"
        if not workspace.is_absolute():
            workspace = root / workspace
        workspace = workspace.resolve()

        workspace_data_dir = workspace / "data"
        return cls(
            root_dir=root,
            workspace_dir=workspace,
            global_tools_dir=root / "global_tools",
            agents_dir=root / "agents_workspace",
            uv_envs_dir=root / "uv_envs",
            checkpoints_db=workspace_data_dir / "checkpoints.sqlite",
            approvals_file=workspace_data_dir / "approvals.json",
            conversations_file=workspace_data_dir / "conversations.json",
            chat_history_dir=workspace_data_dir / "chat_history",
            conversation_offload_dir=workspace / "conversation_history",
            workspace_data_dir=workspace_data_dir,
            workspace_evicted_dir=workspace_data_dir / "evicted",
            apps_dir=workspace / "apps",
            uploads_dir=workspace / "uploads",
            skills_dir=workspace / "skills",
            workflows_dir=workspace / "workflows",
            tools_workspace_dir=root / ".tools_workspace",
        )

    def ensure_runtime_dirs(self) -> None:
        for path in (
            self.workspace_dir,
            self.global_tools_dir,
            self.agents_dir,
            self.uv_envs_dir,
            self.workspace_data_dir,
            self.workspace_evicted_dir,
            self.chat_history_dir,
            self.conversation_offload_dir,
            self.apps_dir,
            self.uploads_dir,
            self.skills_dir,
            self.workflows_dir,
            self.tools_workspace_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
