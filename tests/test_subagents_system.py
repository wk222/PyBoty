"""Unified tests for Subagent system: registry, sandbox, isolation, and runtime (Eighth Round).

Consolidated and merged from:
* test_subagent_registry.py
* test_subagent_sandbox.py
* test_subagent_isolation.py
* test_subagent_runtime_registry.py
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.messages import AIMessage

# Core imports
from core.assets.agents.capability_profile import AgentCapabilityProfile
from core.assets.agents.storage import AgentDefinition
from core.systems.agents import (
    SubagentConcurrencyLimitError,
    SubagentDepthLimitError,
    SubagentRegistry,
)
import core.systems.agents.subagent_registry as registry_module
from core.systems.agents.subagent_runtime import SubAgentRuntime
from core.systems.agents.subagent_sandbox import (
    SubagentSandbox,
    build_subagent_builtin_tools,
    build_subagent_sandbox,
    list_sandbox_adapters,
    resolve_sandbox_adapter,
    supports_execution,
)
from core.systems.governance.agent_control import AgentControlPolicy
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.runtime.backend_protocol import (
    LocalSandboxBackend,
    SandboxBackendProtocol,
)
from core.systems.runtime.event_bus import EventBus, EventType
from core.systems.runtime.project_paths import ProjectPaths
from core.systems.runtime.subagent_isolation import (
    build_root_isolation_descriptor,
    build_root_isolation_projection,
    build_subagent_isolation_descriptor,
    build_subagent_isolation_projection,
    materialize_isolation_execution_options,
)


# ---------------------------------------------------------------------------
# 1. Subagent Registry Tests (formerly test_subagent_registry.py)
# ---------------------------------------------------------------------------

class TestSubagentRegistry:
    def test_subagent_registry_spawn_records_depth_and_emits_event(self, monkeypatch):
        bus = EventBus()
        events = []
        bus.subscribe(EventType.SUBAGENT_SPAWNED, lambda event: events.append(event))
        monkeypatch.setattr(registry_module, "event_bus", bus)

        registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=60)
        record = registry.spawn(
            agent_name="helper",
            thread_id="thread-1",
            parent_agent_name="root",
            parent_depth=0,
        )

        assert record.depth == 1
        assert record.status == "running"
        assert registry.get_active(agent_name="helper", thread_id="thread-1") is record
        assert events[0].payload["agent_name"] == "helper"
        assert events[0].payload["depth"] == 1

    def test_subagent_registry_enforces_depth_limit(self):
        registry = SubagentRegistry(max_depth=2, max_concurrent=5, default_timeout_seconds=60)

        with pytest.raises(SubagentDepthLimitError):
            registry.spawn(
                agent_name="worker",
                thread_id="thread-1",
                parent_agent_name="planner",
                parent_depth=2,
            )

    def test_subagent_registry_enforces_concurrency_limit_for_waiting_runs(self):
        registry = SubagentRegistry(max_depth=3, max_concurrent=1, default_timeout_seconds=60)
        registry.spawn(agent_name="worker_a", thread_id="thread-a")
        registry.mark_waiting_approval(
            agent_name="worker_a",
            thread_id="thread-a",
            approval_id="approval-1",
        )

        with pytest.raises(SubagentConcurrencyLimitError):
            registry.spawn(agent_name="worker_b", thread_id="thread-b")

    def test_subagent_registry_cleanup_marks_timeout_and_emits_event(self, monkeypatch):
        bus = EventBus()
        events = []
        bus.subscribe(EventType.SUBAGENT_TIMEOUT, lambda event: events.append(event))
        monkeypatch.setattr(registry_module, "event_bus", bus)

        registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=1)
        record = registry.spawn(agent_name="worker", thread_id="thread-1")

        timed_out = registry.cleanup_stale(now=record.updated_at + 5)

        assert len(timed_out) == 1
        assert timed_out[0].status == "timed_out"
        assert registry.get_active(agent_name="worker", thread_id="thread-1") is None
        assert registry.get_latest(agent_name="worker", thread_id="thread-1").status == "timed_out"
        assert events[0].payload["status"] == "timed_out"

    def test_subagent_registry_records_steering_and_abort(self):
        registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=60)
        registry.spawn(agent_name="worker", thread_id="thread-1")

        steered = registry.record_steer(
            agent_name="worker",
            thread_id="thread-1",
            instructions="Focus on the failing test first.",
        )
        aborted = registry.abort(
            agent_name="worker",
            thread_id="thread-1",
            reason="operator stop",
        )

        assert steered is not None
        assert steered.steering_instructions == ["Focus on the failing test first."]
        assert aborted is not None
        assert aborted.status == "aborted"
        assert registry.get_active(agent_name="worker", thread_id="thread-1") is None
        assert registry.get_latest(agent_name="worker", thread_id="thread-1").error == "operator stop"

    def test_subagent_registry_builds_team_memory_projection_from_context_notes(self):
        registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=60)
        registry.spawn(
            agent_name="worker",
            thread_id="thread-1",
            team_key="session-1",
            owner_session_key="session-1",
            owner_thread_id="root-thread",
        )
        registry.complete(
            agent_name="worker",
            thread_id="thread-1",
            response="done",
            context_notes=["checked failing tests", "captured stack trace"],
        )
        
        registry.add_team_note(
            team_key="session-1",
            agent_name="coordinator",
            note="Worker finished successfully, proceeding to next task.",
        )

        projection = registry.build_team_memory_projection(
            team_key="session-1",
            owner_session_key="session-1",
            owner_thread_id="root-thread",
        )

        assert projection["team_key"] == "session-1"
        assert projection["shared_memory_ready"] is True
        assert "worker" in projection["participant_agents"]
        assert "coordinator" in projection["participant_agents"]
        assert projection["note_count"] == 3
        assert projection["recent_notes"][-1]["note"] == "Worker finished successfully, proceeding to next task."


# ---------------------------------------------------------------------------
# 2. Subagent Sandbox Tests (formerly test_subagent_sandbox.py)
# ---------------------------------------------------------------------------

class TestSubagentSandbox:
    def test_supports_execution_with_sandbox_backend(self):
        backend = LocalSandboxBackend(".")
        assert supports_execution(backend) is True

    def test_supports_execution_with_plain_object(self):
        assert supports_execution(object()) is False
        assert supports_execution(None) is False

    def test_subagent_sandbox_to_dict_includes_supports_execution(self):
        sandbox = SubagentSandbox(
            mode="restricted",
            visibility="isolated",
            workspace_dir=Path("."),
            allows_writes=True,
            allows_code_execution=True,
            execution_backend=LocalSandboxBackend("."),
        )
        d = sandbox.to_dict()
        assert d["supports_execution"] is True

        sandbox_no_exec = SubagentSandbox(
            mode="restricted",
            visibility="isolated",
            workspace_dir=Path("."),
            allows_writes=False,
            allows_code_execution=False,
        )
        d2 = sandbox_no_exec.to_dict()
        assert d2["supports_execution"] is False

    def test_build_subagent_sandbox_creates_execution_backend_when_allowed(self, tmp_path):
        profile = AgentCapabilityProfile(
            sandbox_mode="restricted",
            sandbox_adapter="isolated",
            allow_code_execution=True,
        )
        paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "ws")
        sandbox = build_subagent_sandbox(
            agent_name="test_agent",
            capability_profile=profile,
            project_paths=paths,
        )
        assert sandbox.allows_code_execution is True
        assert sandbox.execution_backend is not None
        assert isinstance(sandbox.execution_backend, SandboxBackendProtocol)

    def test_build_subagent_sandbox_no_execution_backend_for_read_only(self, tmp_path):
        profile = AgentCapabilityProfile(
            sandbox_mode="read_only",
            sandbox_adapter="workspace",
            allow_code_execution=True,
        )
        paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "ws")
        sandbox = build_subagent_sandbox(
            agent_name="readonly_agent",
            capability_profile=profile,
            project_paths=paths,
        )
        assert sandbox.allows_code_execution is False
        assert sandbox.execution_backend is None

    def test_build_subagent_builtin_tools_includes_exec_with_backend(self, tmp_path):
        profile = AgentCapabilityProfile(
            sandbox_mode="restricted",
            sandbox_adapter="isolated",
            allow_code_execution=True,
        )
        paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "ws")
        sandbox = build_subagent_sandbox(
            agent_name="tools_agent",
            capability_profile=profile,
            project_paths=paths,
        )
        tools = build_subagent_builtin_tools(capability_profile=profile, sandbox=sandbox)
        exec_tools = [t for t in tools if t.name == "exec_code"]
        assert len(exec_tools) == 1
        assert exec_tools[0].sandbox_backend is not None

    def test_build_subagent_builtin_tools_no_exec_for_no_code_execution(self):
        profile = AgentCapabilityProfile(
            sandbox_mode="restricted",
            sandbox_adapter="isolated",
            allow_code_execution=False,
        )
        sandbox = SubagentSandbox(
            mode="restricted",
            visibility="isolated",
            workspace_dir=Path("."),
            allows_writes=True,
            allows_code_execution=False,
        )
        tools = build_subagent_builtin_tools(capability_profile=profile, sandbox=sandbox)
        exec_tools = [t for t in tools if t.name == "exec_code"]
        assert len(exec_tools) == 0

    def test_resolve_sandbox_adapter_auto_restricted(self):
        profile = AgentCapabilityProfile(sandbox_mode="restricted", sandbox_adapter="auto")
        assert resolve_sandbox_adapter(profile) == "isolated"

    def test_resolve_sandbox_adapter_auto_workspace_write(self):
        profile = AgentCapabilityProfile(sandbox_mode="workspace_write", sandbox_adapter="auto")
        assert resolve_sandbox_adapter(profile) == "workspace"

    def test_resolve_sandbox_adapter_explicit(self):
        profile = AgentCapabilityProfile(sandbox_adapter="shared_tools")
        assert resolve_sandbox_adapter(profile) == "shared_tools"

    def test_list_sandbox_adapters_returns_all(self):
        adapters = list_sandbox_adapters()
        names = {a["name"] for a in adapters}
        assert names == {"isolated", "workspace", "shared_tools", "session_scratch", "docker"}

    def test_capability_profile_presets_matrix(self):
        """Verify key presets produce expected capability flags."""
        specialist = AgentCapabilityProfile.from_dict({"preset": "specialist"})
        assert specialist.allow_code_execution is False
        assert specialist.allow_agent_delegation is False


# ---------------------------------------------------------------------------
# 3. Subagent Isolation Tests (formerly test_subagent_isolation.py)
# ---------------------------------------------------------------------------

class TestSubagentIsolation:
    def test_root_isolation_projection_exposes_multi_agent_dependency_chain(self, tmp_path):
        projection = build_root_isolation_projection(
            workspace_dir=tmp_path,
            root_mode="assistant",
            multi_agent_ready=True,
        )

        assert projection["visibility"] == "project"
        assert projection["multi_agent_ready"] is True
        assert projection["delegation_ready"] is True
        assert projection["dependency_chain"][-1] == "isolation_model"

    def test_subagent_isolation_projection_reflects_sandbox_contract(self, tmp_path):
        sandbox = SubagentSandbox(
            mode="read_only",
            visibility="isolated",
            workspace_dir=Path(tmp_path) / "subagent",
            allows_writes=False,
            allows_code_execution=False,
            adapter="isolated",
            backend_name="local",
        )

        projection = build_subagent_isolation_projection(
            agent_name="reviewer",
            sandbox=sandbox,
            thread_id="child-thread",
            parent_thread_id="parent-thread",
            depth=2,
        )

        assert projection["agent_name"] == "reviewer"
        assert projection["visibility"] == "isolated"
        assert projection["allows_writes"] is False
        assert projection["memory_scope"] == "isolated"
        assert projection["permission_scope"] == "subagent:reviewer"
        assert projection["dependency_chain"][-1] == "subagent_runtime"

    def test_root_isolation_descriptor_can_gate_delegation_with_hook_requirements(self, tmp_path):
        class DummyHooksRuntime:
            def run_phase(self, phase, payload):
                return {"requires_strict_isolation": True, "notes": ["strict worker isolation required"]}

        descriptor = build_root_isolation_descriptor(
            workspace_dir=tmp_path,
            root_mode="assistant",
            multi_agent_ready=True,
            thread_id="thread-1",
            hooks_runtime=DummyHooksRuntime(),
        )

        assert descriptor.multi_agent_ready is True
        assert descriptor.delegation_ready is False
        assert descriptor.requires_strict_isolation is True

    def test_subagent_isolation_descriptor_tracks_ownership_contract(self, tmp_path):
        sandbox = SubagentSandbox(
            mode="workspace_write",
            visibility="project",
            workspace_dir=Path(tmp_path) / "project",
            allows_writes=True,
            allows_code_execution=False,
            adapter="workspace",
            backend_name="local",
        )

        descriptor = build_subagent_isolation_descriptor(
            agent_name="builder",
            sandbox=sandbox,
            thread_id="child-thread",
            parent_thread_id="parent-thread",
            owner_session_key="session-1",
            depth=3,
        )

        assert descriptor.owner_thread_id == "parent-thread"
        assert descriptor.owner_session_key == "session-1"
        assert descriptor.artifact_scope == "subagent"
        assert descriptor.audit_scope == "subagent:builder:child-thread"
        assert descriptor.cwd.endswith("project")
        assert descriptor.worktree_dir.endswith("project")
        assert descriptor.repo_root.endswith("project")
        assert descriptor.artifact_owner == "parent-thread"
        assert descriptor.workspace_ready is True
        assert descriptor.artifact_ownership_ready is True
        assert "workspace" in descriptor.writable_domains
        assert "artifacts" in descriptor.readable_domains

    def test_materialized_isolation_execution_options_preserve_contract_fields(self, tmp_path):
        descriptor = build_root_isolation_descriptor(
            workspace_dir=tmp_path,
            root_mode="assistant",
            multi_agent_ready=True,
            thread_id="thread-root",
            session_key="session-root",
        )

        options = materialize_isolation_execution_options(descriptor)

        assert options["cwd"] == str(Path(tmp_path).resolve())
        assert options["worktree_dir"] == str(Path(tmp_path).resolve())
        assert options["artifact_owner"] == "root:thread-root"
        assert "workspace" in options["writable_domains"]


# ---------------------------------------------------------------------------
# 4. Subagent Runtime Tests (formerly test_subagent_runtime_registry.py)
# ---------------------------------------------------------------------------

@dataclass
class _CheckpointBundle:
    checkpointer: object | None = None
    backend: str = "sqlite"
    path: object | None = None

    def close(self) -> None:
        return None


class _Graph:
    def __init__(self, result):
        self._result = result

    def invoke(self, *_args, **_kwargs):
        return self._result


def _sandbox() -> SubagentSandbox:
    return SubagentSandbox(
        mode="restricted",
        visibility="isolated",
        workspace_dir=".",
        allows_writes=True,
        allows_code_execution=False,
    )


def _runtime(*, graph_result, registry: SubagentRegistry) -> SubAgentRuntime:
    return SubAgentRuntime(
        graph=_Graph(graph_result),
        definition=AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help.",
        ),
        tools=[],
        tool_names=["search_notes"],
        control_policy=AgentControlPolicy(),
        sandbox=_sandbox(),
        checkpoint_bundle=_CheckpointBundle(),
        approval_queue=ApprovalQueue(),
        registry=registry,
        runtime_context={
            "session_key": "session-main",
            "owner_thread_id": "root-thread",
            "team_key": "session-main",
        },
    )


class TestSubagentRuntimeRegistry:
    def test_subagent_runtime_marks_completed_runs_in_registry(self):
        registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=60)
        runtime = _runtime(graph_result={"messages": [AIMessage(content="done")]}, registry=registry)

        result = runtime.invoke(
            task="help",
            thread_id="thread-1",
            parent_agent_name="root",
            parent_depth=0,
        )

        assert result["status"] == "completed"
        assert registry.get_active(agent_name="helper", thread_id="thread-1") is None
        latest = registry.get_latest(agent_name="helper", thread_id="thread-1")
        assert latest.status == "completed"
        assert latest.team_key == "session-main"
        assert latest.owner_session_key == "session-main"

    def test_subagent_runtime_waiting_approval_can_be_aborted_without_resume(self, monkeypatch):
        registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=60)
        runtime = _runtime(graph_result={"messages": []}, registry=registry)

        class _PendingApproval:
            approval_id = "approval-1"

        monkeypatch.setattr(runtime, "_register_tool_approval", lambda *_args, **_kwargs: _PendingApproval())

        result = runtime.invoke(
            task="help",
            thread_id="thread-1",
            parent_agent_name="root",
            parent_depth=0,
        )

        assert result["status"] == "waiting_approval"
        assert registry.get_active(agent_name="helper", thread_id="thread-1").status == "waiting_approval"

        aborted = runtime.abort(thread_id="thread-1", reason="operator stop")
        resumed = runtime.resume_approval(
            approval_id="approval-1",
            thread_id="thread-1",
            approved=True,
            note="resume",
        )

        assert aborted["status"] == "aborted"
        assert resumed["status"] == "aborted"
