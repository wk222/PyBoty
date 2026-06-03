from __future__ import annotations

from pathlib import Path

from core.systems.governance.subagent_sandbox import SubagentSandbox
from core.systems.runtime.subagent_isolation import (
    build_root_isolation_descriptor,
    build_root_isolation_projection,
    build_subagent_isolation_descriptor,
    build_subagent_isolation_projection,
    materialize_isolation_execution_options,
)


def test_root_isolation_projection_exposes_multi_agent_dependency_chain(tmp_path):
    projection = build_root_isolation_projection(
        workspace_dir=tmp_path,
        root_mode="assistant",
        multi_agent_ready=True,
    )

    assert projection["visibility"] == "project"
    assert projection["multi_agent_ready"] is True
    assert projection["delegation_ready"] is True
    assert projection["dependency_chain"][-1] == "isolation_model"


def test_subagent_isolation_projection_reflects_sandbox_contract(tmp_path):
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


def test_root_isolation_descriptor_can_gate_delegation_with_hook_requirements(tmp_path):
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


def test_subagent_isolation_descriptor_tracks_ownership_contract(tmp_path):
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


def test_materialized_isolation_execution_options_preserve_contract_fields(tmp_path):
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
