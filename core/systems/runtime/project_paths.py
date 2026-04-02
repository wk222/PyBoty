"""
Project path helpers.

Centralizes source/runtime boundaries so entrypoints do not scatter path logic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_RUNTIME_HOME_ENV = "PYBOT_RUNTIME_HOME"


def _default_runtime_root(explicit_root: Path | None = None) -> Path:
    """Pick the default runtime home.

    If a test or caller passes an explicit ``root_dir``, keep the runtime
    colocated there for deterministic local fixtures. Otherwise default to a
    user-scoped runtime home so the repo root stops accumulating live state.
    """
    if explicit_root is not None:
        return explicit_root

    configured = os.environ.get(_RUNTIME_HOME_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        return (Path(local_appdata) / "PyBot").resolve()

    return (Path.home() / ".pybot").resolve()


@dataclass(frozen=True)
class ProjectPaths:
    root_dir: Path
    runtime_root_dir: Path
    workspace_dir: Path
    global_tools_dir: Path
    agents_dir: Path
    uv_envs_dir: Path
    checkpoints_db: Path
    approvals_file: Path
    conversations_file: Path
    sessions_file: Path
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
        runtime_root_dir: str | Path | None = None,
    ) -> ProjectPaths:
        explicit_root = Path(root_dir).resolve() if root_dir else None
        root = explicit_root or _PROJECT_ROOT
        root = root.resolve()

        if runtime_root_dir is None:
            runtime_root = _default_runtime_root(explicit_root)
        else:
            runtime_root = Path(runtime_root_dir)
            if not runtime_root.is_absolute():
                runtime_root = root / runtime_root
            runtime_root = runtime_root.resolve()

        workspace = Path(workspace_dir) if workspace_dir else runtime_root / "workspace"
        if not workspace.is_absolute():
            workspace = runtime_root / workspace
        workspace = workspace.resolve()

        workspace_data_dir = workspace / "data"
        return cls(
            root_dir=root,
            runtime_root_dir=runtime_root,
            workspace_dir=workspace,
            global_tools_dir=runtime_root / "global_tools",
            agents_dir=runtime_root / "agents_workspace",
            uv_envs_dir=runtime_root / "uv_envs",
            checkpoints_db=workspace_data_dir / "checkpoints.sqlite",
            approvals_file=workspace_data_dir / "approvals.json",
            conversations_file=workspace_data_dir / "conversations.json",
            sessions_file=workspace_data_dir / "sessions.json",
            chat_history_dir=workspace_data_dir / "chat_history",
            conversation_offload_dir=workspace / "conversation_history",
            workspace_data_dir=workspace_data_dir,
            workspace_evicted_dir=workspace_data_dir / "evicted",
            apps_dir=workspace / "apps",
            uploads_dir=workspace / "uploads",
            skills_dir=workspace / "skills",
            workflows_dir=workspace / "workflows",
            tools_workspace_dir=runtime_root / ".tools_workspace",
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
