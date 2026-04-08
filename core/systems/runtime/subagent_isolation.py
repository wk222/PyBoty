"""Canonical isolation descriptors for root and subagent runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.systems.runtime.hooks_runtime import HookPhase


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        item = _as_text(raw)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


@dataclass(frozen=True)
class IsolationDescriptor:
    surface: str
    adapter: str
    backend: str
    visibility: str
    workspace_dir: str
    cwd: str
    worktree_dir: str
    repo_root: str
    remote_target: str
    allows_writes: bool
    allows_code_execution: bool
    supports_execution: bool
    multi_agent_ready: bool
    delegation_ready: bool
    isolation_ready: bool
    permission_ready: bool
    workspace_ready: bool
    artifact_ownership_ready: bool
    recovery_ready: bool
    permission_scope: str
    artifact_scope: str
    artifact_owner: str
    owner_run_id: str
    memory_scope: str
    audit_scope: str
    tool_scope: list[str] = field(default_factory=list)
    writable_domains: list[str] = field(default_factory=list)
    readable_domains: list[str] = field(default_factory=list)
    dependency_chain: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    summary: str = ""
    agent_name: str = ""
    thread_id: str = ""
    parent_thread_id: str = ""
    owner_thread_id: str = ""
    owner_session_key: str = ""
    depth: int = 0
    requires_strict_isolation: bool = False
    requires_workspace_visibility: bool = False

    def to_projection(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "surface": self.surface,
            "agent_name": self.agent_name,
            "thread_id": self.thread_id,
            "parent_thread_id": self.parent_thread_id,
            "owner_thread_id": self.owner_thread_id,
            "owner_session_key": self.owner_session_key,
            "depth": int(self.depth),
            "adapter": self.adapter,
            "backend": self.backend,
            "visibility": self.visibility,
            "workspace_dir": self.workspace_dir,
            "cwd": self.cwd,
            "worktree_dir": self.worktree_dir,
            "repo_root": self.repo_root,
            "remote_target": self.remote_target,
            "allows_writes": self.allows_writes,
            "allows_code_execution": self.allows_code_execution,
            "supports_execution": self.supports_execution,
            "multi_agent_ready": self.multi_agent_ready,
            "delegation_ready": self.delegation_ready,
            "isolation_ready": self.isolation_ready,
            "permission_ready": self.permission_ready,
            "workspace_ready": self.workspace_ready,
            "artifact_ownership_ready": self.artifact_ownership_ready,
            "recovery_ready": self.recovery_ready,
            "permission_scope": self.permission_scope,
            "artifact_scope": self.artifact_scope,
            "artifact_owner": self.artifact_owner,
            "owner_run_id": self.owner_run_id,
            "memory_scope": self.memory_scope,
            "audit_scope": self.audit_scope,
            "tool_scope": list(self.tool_scope),
            "writable_domains": list(self.writable_domains),
            "readable_domains": list(self.readable_domains),
            "dependency_chain": list(self.dependency_chain),
            "labels": list(self.labels),
            "notes": list(self.notes),
            "requires_strict_isolation": self.requires_strict_isolation,
            "requires_workspace_visibility": self.requires_workspace_visibility,
        }


def _memory_scope_for_visibility(visibility: str) -> str:
    normalized = _as_text(visibility) or "isolated"
    return {
        "project": "project",
        "shared_tools": "shared_tools",
        "session": "session",
        "isolated": "isolated",
    }.get(normalized, normalized)


def _tool_scope_for_descriptor(*, surface: str, allows_writes: bool, allows_code_execution: bool) -> list[str]:
    scope = ["read"]
    if allows_writes:
        scope.append("write")
    if allows_code_execution:
        scope.append("exec")
    if surface == "single_agent_runtime":
        scope.append("delegate")
    return scope


def _domains_for_descriptor(*, allows_writes: bool, surface: str) -> tuple[list[str], list[str]]:
    readable = ["workspace", "artifacts", "memory"]
    writable = ["artifacts"]
    if allows_writes:
        writable.append("workspace")
    if surface == "single_agent_runtime":
        readable.append("governance")
        writable.append("governance")
    return _dedupe_strings(writable), _dedupe_strings(readable)


def _apply_hook_contract(
    descriptor: IsolationDescriptor,
    *,
    hooks_runtime: Any | None = None,
    projected_runtime_view: dict[str, Any] | None = None,
    sandbox: dict[str, Any] | None = None,
) -> IsolationDescriptor:
    if hooks_runtime is None or not hasattr(hooks_runtime, "run_phase"):
        return descriptor
    try:
        hook_result = hooks_runtime.run_phase(
            HookPhase.SUBAGENT_ISOLATION,
            {
                "agent_name": descriptor.agent_name,
                "surface": descriptor.surface,
                "sandbox": dict(sandbox or {}),
                "projected_runtime_view": dict(projected_runtime_view or {}),
            },
        )
    except Exception:
        return descriptor

    labels = _dedupe_strings(list(descriptor.labels) + list(hook_result.get("labels", [])))
    notes = _dedupe_strings(list(descriptor.notes) + list(hook_result.get("notes", [])))
    requires_strict_isolation = bool(descriptor.requires_strict_isolation) or bool(
        hook_result.get("requires_strict_isolation")
    )
    requires_workspace_visibility = bool(descriptor.requires_workspace_visibility) or bool(
        hook_result.get("requires_workspace_visibility")
    )
    strict_ok = (not requires_strict_isolation) or descriptor.visibility == "isolated"
    workspace_visibility_ok = (not requires_workspace_visibility) or descriptor.visibility == "project"
    workspace_ready = bool(descriptor.workspace_ready) and workspace_visibility_ok
    delegation_ready = (
        bool(descriptor.multi_agent_ready)
        and bool(descriptor.isolation_ready)
        and bool(descriptor.permission_ready)
        and workspace_ready
        and bool(descriptor.artifact_ownership_ready)
        and bool(descriptor.recovery_ready)
        and strict_ok
    )
    summary = descriptor.summary
    if notes:
        summary = f"{summary}; {'; '.join(notes[:2])}" if summary else "; ".join(notes[:2])
    return IsolationDescriptor(
        **{
            **descriptor.__dict__,
            "labels": labels,
            "notes": notes,
            "requires_strict_isolation": requires_strict_isolation,
            "requires_workspace_visibility": requires_workspace_visibility,
            "workspace_ready": workspace_ready,
            "delegation_ready": delegation_ready,
            "summary": summary,
        }
    )


def build_root_isolation_descriptor(
    *,
    workspace_dir: str | Path,
    root_mode: str,
    multi_agent_ready: bool,
    thread_id: str = "",
    session_key: str = "",
    hooks_runtime: Any | None = None,
    projected_runtime_view: dict[str, Any] | None = None,
) -> IsolationDescriptor:
    resolved_workspace = str(Path(workspace_dir).resolve())
    writable_domains, readable_domains = _domains_for_descriptor(
        allows_writes=True,
        surface="single_agent_runtime",
    )
    summary = f"root runtime ({_as_text(root_mode) or 'assistant'}) uses the project workspace"
    summary += "; subagent isolation chain is available" if multi_agent_ready else "; subagent isolation chain is not ready"
    descriptor = IsolationDescriptor(
        surface="single_agent_runtime",
        adapter="workspace",
        backend="local",
        visibility="project",
        workspace_dir=resolved_workspace,
        cwd=resolved_workspace,
        worktree_dir=resolved_workspace,
        repo_root=resolved_workspace,
        remote_target="",
        allows_writes=True,
        allows_code_execution=False,
        supports_execution=False,
        multi_agent_ready=bool(multi_agent_ready),
        delegation_ready=bool(multi_agent_ready),
        isolation_ready=True,
        permission_ready=True,
        workspace_ready=bool(resolved_workspace),
        artifact_ownership_ready=bool(_as_text(thread_id) or _as_text(session_key)),
        recovery_ready=bool(_as_text(thread_id) or _as_text(session_key)),
        permission_scope=f"root:{_as_text(thread_id) or 'default'}",
        artifact_scope="session",
        artifact_owner=f"root:{_as_text(thread_id) or 'default'}",
        owner_run_id="",
        memory_scope="project",
        audit_scope=f"root:{_as_text(thread_id) or 'default'}",
        tool_scope=_tool_scope_for_descriptor(
            surface="single_agent_runtime",
            allows_writes=True,
            allows_code_execution=False,
        ),
        writable_domains=writable_domains,
        readable_domains=readable_domains,
        dependency_chain=[
            "single_agent_runtime",
            "task_runtime",
            "permission_control_plane",
            "isolation_model",
        ],
        labels=["surface:single_agent_runtime", "visibility:project"],
        notes=[],
        summary=summary,
        owner_thread_id=_as_text(thread_id),
        owner_session_key=_as_text(session_key),
    )
    return _apply_hook_contract(
        descriptor,
        hooks_runtime=hooks_runtime,
        projected_runtime_view=projected_runtime_view,
        sandbox=descriptor.to_projection(),
    )


def build_root_isolation_projection(**kwargs: Any) -> dict[str, Any]:
    return build_root_isolation_descriptor(**kwargs).to_projection()


def build_subagent_isolation_descriptor(
    *,
    agent_name: str,
    sandbox: Any,
    thread_id: str = "",
    parent_thread_id: str = "",
    depth: int = 0,
    owner_session_key: str = "",
    hooks_runtime: Any | None = None,
    projected_runtime_view: dict[str, Any] | None = None,
) -> IsolationDescriptor:
    sandbox_dict = sandbox.to_dict() if hasattr(sandbox, "to_dict") else dict(sandbox or {})
    visibility = _as_text(sandbox_dict.get("visibility")) or "isolated"
    adapter = _as_text(sandbox_dict.get("adapter")) or "isolated"
    workspace_dir = _as_text(sandbox_dict.get("workspace_dir"))
    cwd = _as_text(sandbox_dict.get("cwd")) or workspace_dir
    worktree_dir = _as_text(sandbox_dict.get("worktree_dir")) or workspace_dir
    repo_root = _as_text(sandbox_dict.get("repo_root")) or workspace_dir
    remote_target = _as_text(sandbox_dict.get("remote_target"))
    allows_writes = bool(sandbox_dict.get("allows_writes"))
    allows_code_execution = bool(sandbox_dict.get("allows_code_execution"))
    supports_execution = bool(sandbox_dict.get("supports_execution"))
    writable_domains, readable_domains = _domains_for_descriptor(
        allows_writes=allows_writes,
        surface="subagent_runtime",
    )
    artifact_owner = _as_text(parent_thread_id) or _as_text(thread_id) or _as_text(agent_name)
    isolation_ready = visibility in {"isolated", "project", "shared_tools", "session"}
    workspace_ready = bool(workspace_dir)
    artifact_ownership_ready = bool(artifact_owner and (_as_text(owner_session_key) or _as_text(thread_id)))
    recovery_ready = bool(_as_text(thread_id))
    summary = (
        f"{_as_text(agent_name) or 'subagent'} runs with {visibility}/{adapter} isolation "
        f"(writes={'on' if allows_writes else 'off'}, exec={'on' if allows_code_execution else 'off'})"
    )
    descriptor = IsolationDescriptor(
        surface="subagent_runtime",
        adapter=adapter,
        backend=_as_text(sandbox_dict.get("backend")) or "local",
        visibility=visibility,
        workspace_dir=workspace_dir,
        cwd=cwd,
        worktree_dir=worktree_dir,
        repo_root=repo_root,
        remote_target=remote_target,
        allows_writes=allows_writes,
        allows_code_execution=allows_code_execution,
        supports_execution=supports_execution,
        multi_agent_ready=True,
        delegation_ready=False,
        isolation_ready=isolation_ready,
        permission_ready=True,
        workspace_ready=workspace_ready,
        artifact_ownership_ready=artifact_ownership_ready,
        recovery_ready=recovery_ready,
        permission_scope=f"subagent:{_as_text(agent_name) or 'worker'}",
        artifact_scope="subagent",
        artifact_owner=artifact_owner,
        owner_run_id="",
        memory_scope=_memory_scope_for_visibility(visibility),
        audit_scope=f"subagent:{_as_text(agent_name) or 'worker'}:{_as_text(thread_id) or 'default'}",
        tool_scope=_tool_scope_for_descriptor(
            surface="subagent_runtime",
            allows_writes=allows_writes,
            allows_code_execution=allows_code_execution,
        ),
        writable_domains=writable_domains,
        readable_domains=readable_domains,
        dependency_chain=[
            "single_agent_runtime",
            "task_runtime",
            "permission_control_plane",
            "isolation_model",
            "subagent_runtime",
        ],
        labels=[f"surface:subagent_runtime", f"visibility:{visibility}"],
        notes=[],
        summary=summary,
        agent_name=_as_text(agent_name),
        thread_id=_as_text(thread_id),
        parent_thread_id=_as_text(parent_thread_id),
        owner_thread_id=_as_text(parent_thread_id) or _as_text(thread_id),
        owner_session_key=_as_text(owner_session_key),
        depth=max(0, int(depth or 0)),
    )
    return _apply_hook_contract(
        descriptor,
        hooks_runtime=hooks_runtime,
        projected_runtime_view=projected_runtime_view,
        sandbox=sandbox_dict,
    )


def build_subagent_isolation_projection(**kwargs: Any) -> dict[str, Any]:
    return build_subagent_isolation_descriptor(**kwargs).to_projection()


def materialize_isolation_execution_options(
    descriptor: IsolationDescriptor | dict[str, Any] | None,
) -> dict[str, Any]:
    payload = descriptor.to_projection() if isinstance(descriptor, IsolationDescriptor) else dict(descriptor or {})
    return {
        "cwd": _as_text(payload.get("cwd")),
        "worktree_dir": _as_text(payload.get("worktree_dir")),
        "repo_root": _as_text(payload.get("repo_root")),
        "remote_target": _as_text(payload.get("remote_target")),
        "writable_domains": _dedupe_strings(list(payload.get("writable_domains", []))),
        "readable_domains": _dedupe_strings(list(payload.get("readable_domains", []))),
        "artifact_owner": _as_text(payload.get("artifact_owner")),
        "owner_run_id": _as_text(payload.get("owner_run_id")),
    }


__all__ = [
    "IsolationDescriptor",
    "build_root_isolation_descriptor",
    "build_root_isolation_projection",
    "build_subagent_isolation_descriptor",
    "build_subagent_isolation_projection",
    "materialize_isolation_execution_options",
]
