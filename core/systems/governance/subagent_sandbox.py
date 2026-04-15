"""Sandbox and built-in tool helpers for persisted subagents."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.modes.agents.agent_capability_profile import AgentCapabilityProfile
from core.systems.runtime.backend_protocol import LocalSandboxBackend, SandboxBackendProtocol
from core.systems.execution.execution_loop import ExecCodeTool, IterativeFixTool, ScanProjectTool
from core.systems.runtime.project_paths import ProjectPaths


def supports_execution(backend: Any) -> bool:
    """Check whether a backend supports code execution (deepagents-style capability probe)."""
    return isinstance(backend, SandboxBackendProtocol)


@dataclass(frozen=True)
class SubagentSandbox:
    """Resolved execution sandbox for a persisted subagent."""

    mode: str
    visibility: str
    workspace_dir: Path
    allows_writes: bool
    allows_code_execution: bool
    adapter: str = "isolated"
    backend_name: str = "local"
    execution_backend: SandboxBackendProtocol | None = field(default=None, repr=False)

    def ensure_dirs(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "adapter": self.adapter,
            "backend": self.backend_name,
            "visibility": self.visibility,
            "workspace_dir": str(self.workspace_dir),
            "allows_writes": self.allows_writes,
            "allows_code_execution": self.allows_code_execution,
            "supports_execution": self.execution_backend is not None,
        }


def build_subagent_sandbox(
    *,
    agent_name: str,
    capability_profile: AgentCapabilityProfile,
    project_paths: ProjectPaths | None = None,
) -> SubagentSandbox:
    """Resolve the effective sandbox workspace for a persisted subagent."""
    sandbox_root = (
        project_paths.tools_workspace_dir / "subagents" / agent_name / "workspace"
        if project_paths is not None
        else Path(".tools_workspace") / "subagents" / agent_name / "workspace"
    ).resolve()
    shared_root = (
        project_paths.tools_workspace_dir / "shared"
        if project_paths is not None
        else Path(".tools_workspace") / "shared"
    ).resolve()
    session_root = (
        project_paths.tools_workspace_dir / "sessions" / agent_name
        if project_paths is not None
        else Path(".tools_workspace") / "sessions" / agent_name
    ).resolve()
    adapter = resolve_sandbox_adapter(capability_profile)

    if adapter == "workspace" and project_paths is not None:
        workspace_dir = project_paths.workspace_dir.resolve()
        visibility = "project"
    elif adapter == "shared_tools":
        workspace_dir = shared_root
        visibility = "shared_tools"
    elif adapter == "session_scratch":
        workspace_dir = session_root
        visibility = "session"
    else:
        workspace_dir = sandbox_root
        visibility = "isolated"

    if adapter == "workspace":
        allows_writes = capability_profile.sandbox_mode == "workspace_write"
    elif capability_profile.sandbox_mode == "read_only":
        allows_writes = False
    else:
        allows_writes = True

    can_execute = capability_profile.allow_code_execution and allows_writes
    backend_name = "local"
    execution_backend = None
    if can_execute:
        if adapter == "docker":
            try:
                from .docker_sandbox import DockerSandboxBackend, is_docker_available

                if is_docker_available():
                    execution_backend = DockerSandboxBackend(
                        root_dir="/workspace",
                        container_name=f"pybot-{agent_name}",
                    )
                    backend_name = "docker"
            except ImportError:
                pass
        if execution_backend is None:
            execution_backend = LocalSandboxBackend(str(workspace_dir))
    sandbox = SubagentSandbox(
        mode=capability_profile.sandbox_mode,
        adapter=adapter,
        backend_name=backend_name,
        visibility=visibility,
        workspace_dir=workspace_dir,
        allows_writes=allows_writes,
        allows_code_execution=can_execute,
        execution_backend=execution_backend,
    )
    sandbox.ensure_dirs()
    return sandbox


def build_subagent_builtin_tools(
    *,
    capability_profile: AgentCapabilityProfile,
    sandbox: SubagentSandbox,
) -> list[Any]:
    """Create built-in subagent tools constrained by the effective sandbox."""
    tools: list[Any] = []
    ws = str(sandbox.workspace_dir)

    if sandbox.visibility == "project" or sandbox.allows_code_execution:
        tools.append(ScanProjectTool(workspace_dir=ws))

    if sandbox.allows_code_execution:
        tool_kwargs: dict[str, Any] = {"workspace_dir": ws}
        if sandbox.execution_backend is not None:
            tool_kwargs["sandbox_backend"] = sandbox.execution_backend
        tools.append(ExecCodeTool(**tool_kwargs))
        if sandbox.visibility == "project":
            tools.append(IterativeFixTool(workspace_dir=ws))

    return tools


def resolve_sandbox_adapter(capability_profile: AgentCapabilityProfile) -> str:
    requested = capability_profile.sandbox_adapter.strip().lower()
    if requested and requested != "auto":
        return requested
    if capability_profile.sandbox_mode in {"workspace_write", "read_only"}:
        return "workspace"
    return "isolated"


def list_sandbox_adapters() -> list[dict[str, str]]:
    return [
        {
            "name": "isolated",
            "backend": "local",
            "description": "Agent-scoped local workspace under .tools_workspace/subagents.",
        },
        {
            "name": "workspace",
            "backend": "local",
            "description": "Main project workspace, optionally read-only or write-enabled depending on sandbox mode.",
        },
        {
            "name": "shared_tools",
            "backend": "local",
            "description": "Shared local coordination area under .tools_workspace/shared for cross-agent handoffs.",
        },
        {
            "name": "session_scratch",
            "backend": "local",
            "description": "Session-style scratch space under .tools_workspace/sessions for ephemeral execution runs.",
        },
        {
            "name": "docker",
            "backend": "docker",
            "description": "Docker container sandbox for isolated code execution. Requires Docker.",
        },
    ]

