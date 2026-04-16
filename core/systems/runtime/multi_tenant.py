"""Multi-tenant workspace isolation.

Provides per-user workspace management so multiple users can share
a single PyBot instance while keeping their tools, memory, conversations,
and files completely isolated.

Each tenant gets:
  - Isolated workspace directory (tools, skills, memory, uploads)
  - Independent memory system (MEMORY.md, daily journals, garden)
  - Separate conversation threads
  - Own agent instances

Tenant resolution priority:
  1. X-Tenant-ID header
  2. API key → tenant mapping from config
  3. Default tenant ("default")
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TenantProfile:
    """Configuration and metadata for a single tenant."""

    tenant_id: str
    display_name: str = ""
    workspace_root: str = ""
    max_tools: int = 100
    max_conversations: int = 1000
    max_memory_lines: int = 5000
    canvas_default: str = "balanced"
    model_overrides: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "display_name": self.display_name or self.tenant_id,
            "workspace_root": self.workspace_root,
            "max_tools": self.max_tools,
            "max_conversations": self.max_conversations,
            "canvas_default": self.canvas_default,
            "enabled": self.enabled,
        }


@dataclass
class TenantWorkspace:
    """Resolved workspace paths for a tenant."""

    tenant_id: str
    root: Path
    tools_dir: Path
    skills_dir: Path
    agents_dir: Path
    workflows_dir: Path
    apps_dir: Path
    memory_dir: Path
    uploads_dir: Path
    db_path: Path

    @classmethod
    def create(cls, tenant_id: str, base_dir: Path) -> "TenantWorkspace":
        root = base_dir / "tenants" / tenant_id
        ws = cls(
            tenant_id=tenant_id,
            root=root,
            tools_dir=root / "tools",
            skills_dir=root / "skills",
            agents_dir=root / "agents",
            workflows_dir=root / "workflows",
            apps_dir=root / "apps",
            memory_dir=root / "memory",
            uploads_dir=root / "uploads",
            db_path=root / "pybot.db",
        )
        ws.ensure_dirs()
        return ws

    def ensure_dirs(self) -> None:
        for d in [
            self.root, self.tools_dir, self.skills_dir,
            self.agents_dir, self.workflows_dir, self.apps_dir,
            self.memory_dir, self.uploads_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


class TenantManager:
    """Manages tenant profiles and workspace resolution."""

    DEFAULT_TENANT = "default"

    def __init__(
        self,
        base_dir: str | Path,
        config: dict[str, Any] | None = None,
    ):
        self._base_dir = Path(base_dir)
        self._config = config or {}
        self._profiles: dict[str, TenantProfile] = {}
        self._workspaces: dict[str, TenantWorkspace] = {}
        self._api_key_map: dict[str, str] = {}

        self._load_config()

    def _load_config(self) -> None:
        tenants_cfg = self._config.get("tenants", {})
        for tid, tcfg in tenants_cfg.items():
            self._profiles[tid] = TenantProfile(
                tenant_id=tid,
                display_name=tcfg.get("display_name", tid),
                workspace_root=tcfg.get("workspace_root", ""),
                max_tools=tcfg.get("max_tools", 100),
                max_conversations=tcfg.get("max_conversations", 1000),
                canvas_default=tcfg.get("canvas_default", "balanced"),
                model_overrides=tcfg.get("model_overrides", {}),
                enabled=tcfg.get("enabled", True),
            )

        key_map = self._config.get("api_key_tenants", {})
        for key, tid in key_map.items():
            self._api_key_map[key] = tid

    def resolve_tenant(
        self,
        *,
        header_tenant: str | None = None,
        api_key: str | None = None,
    ) -> str:
        """Resolve the active tenant ID from request context."""
        if header_tenant:
            return header_tenant

        if api_key and api_key in self._api_key_map:
            return self._api_key_map[api_key]

        return self.DEFAULT_TENANT

    def get_workspace(self, tenant_id: str) -> TenantWorkspace:
        """Get or create workspace for a tenant."""
        if tenant_id not in self._workspaces:
            profile = self._profiles.get(tenant_id)
            if profile and profile.workspace_root:
                base = Path(profile.workspace_root)
            else:
                base = self._base_dir
            self._workspaces[tenant_id] = TenantWorkspace.create(tenant_id, base)
        return self._workspaces[tenant_id]

    def get_profile(self, tenant_id: str) -> TenantProfile:
        """Get tenant profile, creating a default one if needed."""
        if tenant_id not in self._profiles:
            self._profiles[tenant_id] = TenantProfile(tenant_id=tenant_id)
        return self._profiles[tenant_id]

    def list_tenants(self) -> list[dict[str, Any]]:
        tenants_dir = self._base_dir / "tenants"
        known = set(self._profiles.keys())
        if tenants_dir.exists():
            for d in tenants_dir.iterdir():
                if d.is_dir():
                    known.add(d.name)
        return [self.get_profile(tid).to_dict() for tid in sorted(known)]

    def delete_tenant(self, tenant_id: str) -> bool:
        """Remove tenant profile (workspace files are NOT deleted for safety)."""
        if tenant_id == self.DEFAULT_TENANT:
            return False
        self._profiles.pop(tenant_id, None)
        self._workspaces.pop(tenant_id, None)
        self._api_key_map = {k: v for k, v in self._api_key_map.items() if v != tenant_id}
        return True

    def get_stats(self, tenant_id: str) -> dict[str, Any]:
        """Get usage stats for a tenant."""
        ws = self.get_workspace(tenant_id)
        def count_files(d: Path) -> int:
            if not d.exists():
                return 0
            return sum(1 for _ in d.rglob("*") if _.is_file())

        return {
            "tenant_id": tenant_id,
            "tools": count_files(ws.tools_dir),
            "skills": count_files(ws.skills_dir),
            "agents": count_files(ws.agents_dir),
            "memory_files": count_files(ws.memory_dir),
            "uploads": count_files(ws.uploads_dir),
            "db_exists": ws.db_path.exists(),
        }


def create_tenant_manager(
    base_dir: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> TenantManager:
    """Factory for TenantManager with sensible defaults."""
    if base_dir is None:
        base_dir = os.environ.get("PYBOT_RUNTIME_HOME", ".")
    return TenantManager(base_dir=base_dir, config=config or {})
