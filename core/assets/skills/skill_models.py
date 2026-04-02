"""Shared models for skill discovery and runtime."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillDefinition:
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "system"
    homepage: str = ""
    skill_format: str = "pybot"
    capabilities: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    system_prompt_extension: str = ""
    enabled: bool = True
    user_invocable: bool = False
    installed_at: float = field(default_factory=time.time)
    uv_dependencies: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    openclaw_metadata: dict[str, Any] = field(default_factory=dict)
    requires_bins: list[str] = field(default_factory=list)
    requires_config: list[str] = field(default_factory=list)
    primary_env: str = ""
    source_name: str = "workspace"
    source_backend: str = "filesystem"
    source_path: str = ""
    skill_path: str = ""
    skill_dir: str = ""
    writable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "homepage": self.homepage,
            "skill_format": self.skill_format,
            "capabilities": self.capabilities,
            "tools": [{key: value for key, value in tool.items() if key != "_tool_instance"} for tool in self.tools],
            "system_prompt_extension": self.system_prompt_extension,
            "enabled": self.enabled,
            "user_invocable": self.user_invocable,
            "installed_at": self.installed_at,
            "uv_dependencies": self.uv_dependencies,
            "metadata": self.metadata,
            "openclaw_metadata": self.openclaw_metadata,
            "requires_bins": self.requires_bins,
            "requires_config": self.requires_config,
            "primary_env": self.primary_env,
            "source_name": self.source_name,
            "source_backend": self.source_backend,
            "source_path": self.source_path,
            "skill_path": self.skill_path,
            "skill_dir": self.skill_dir,
            "writable": self.writable,
        }
