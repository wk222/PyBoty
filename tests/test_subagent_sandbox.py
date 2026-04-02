"""Tests for subagent sandbox, execution backend, and capability matrix."""

from __future__ import annotations

from core.assets.agents.agent_capability_profile import AgentCapabilityProfile
from core.systems.runtime.backend_protocol import LocalSandboxBackend, SandboxBackendProtocol
from core.systems.governance.subagent_sandbox import (
    SubagentSandbox,
    build_subagent_builtin_tools,
    build_subagent_sandbox,
    list_sandbox_adapters,
    resolve_sandbox_adapter,
    supports_execution,
)


def test_supports_execution_with_sandbox_backend():
    backend = LocalSandboxBackend(".")
    assert supports_execution(backend) is True


def test_supports_execution_with_plain_object():
    assert supports_execution(object()) is False
    assert supports_execution(None) is False


def test_subagent_sandbox_to_dict_includes_supports_execution():
    sandbox = SubagentSandbox(
        mode="restricted",
        visibility="isolated",
        workspace_dir=__import__("pathlib").Path("."),
        allows_writes=True,
        allows_code_execution=True,
        execution_backend=LocalSandboxBackend("."),
    )
    d = sandbox.to_dict()
    assert d["supports_execution"] is True

    sandbox_no_exec = SubagentSandbox(
        mode="restricted",
        visibility="isolated",
        workspace_dir=__import__("pathlib").Path("."),
        allows_writes=False,
        allows_code_execution=False,
    )
    d2 = sandbox_no_exec.to_dict()
    assert d2["supports_execution"] is False


def test_build_subagent_sandbox_creates_execution_backend_when_allowed(tmp_path):
    profile = AgentCapabilityProfile(
        sandbox_mode="restricted",
        sandbox_adapter="isolated",
        allow_code_execution=True,
    )
    from core.systems.runtime.project_paths import ProjectPaths

    paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "ws")
    sandbox = build_subagent_sandbox(
        agent_name="test_agent",
        capability_profile=profile,
        project_paths=paths,
    )
    assert sandbox.allows_code_execution is True
    assert sandbox.execution_backend is not None
    assert isinstance(sandbox.execution_backend, SandboxBackendProtocol)


def test_build_subagent_sandbox_no_execution_backend_for_read_only(tmp_path):
    profile = AgentCapabilityProfile(
        sandbox_mode="read_only",
        sandbox_adapter="workspace",
        allow_code_execution=True,
    )
    from core.systems.runtime.project_paths import ProjectPaths

    paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "ws")
    sandbox = build_subagent_sandbox(
        agent_name="readonly_agent",
        capability_profile=profile,
        project_paths=paths,
    )
    assert sandbox.allows_code_execution is False
    assert sandbox.execution_backend is None


def test_build_subagent_builtin_tools_includes_exec_with_backend(tmp_path):
    profile = AgentCapabilityProfile(
        sandbox_mode="restricted",
        sandbox_adapter="isolated",
        allow_code_execution=True,
    )
    from core.systems.runtime.project_paths import ProjectPaths

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


def test_build_subagent_builtin_tools_no_exec_for_no_code_execution():
    profile = AgentCapabilityProfile(
        sandbox_mode="restricted",
        sandbox_adapter="isolated",
        allow_code_execution=False,
    )
    sandbox = SubagentSandbox(
        mode="restricted",
        visibility="isolated",
        workspace_dir=__import__("pathlib").Path("."),
        allows_writes=True,
        allows_code_execution=False,
    )
    tools = build_subagent_builtin_tools(capability_profile=profile, sandbox=sandbox)
    exec_tools = [t for t in tools if t.name == "exec_code"]
    assert len(exec_tools) == 0


def test_resolve_sandbox_adapter_auto_restricted():
    profile = AgentCapabilityProfile(sandbox_mode="restricted", sandbox_adapter="auto")
    assert resolve_sandbox_adapter(profile) == "isolated"


def test_resolve_sandbox_adapter_auto_workspace_write():
    profile = AgentCapabilityProfile(sandbox_mode="workspace_write", sandbox_adapter="auto")
    assert resolve_sandbox_adapter(profile) == "workspace"


def test_resolve_sandbox_adapter_explicit():
    profile = AgentCapabilityProfile(sandbox_adapter="shared_tools")
    assert resolve_sandbox_adapter(profile) == "shared_tools"


def test_list_sandbox_adapters_returns_all():
    adapters = list_sandbox_adapters()
    names = {a["name"] for a in adapters}
    assert names == {"isolated", "workspace", "shared_tools", "session_scratch", "docker"}


def test_capability_profile_presets_matrix():
    """Verify key presets produce expected capability flags."""
    specialist = AgentCapabilityProfile.from_dict({"preset": "specialist"})
    assert specialist.allow_code_execution is False
    assert specialist.allow_agent_delegation is False

    builder = AgentCapabilityProfile.from_dict({"preset": "builder"})
    assert builder.allow_code_execution is True
    assert builder.allow_local_tool_creation is True

    manager = AgentCapabilityProfile.from_dict({"preset": "manager"})
    assert manager.allow_agent_delegation is True
    assert manager.allow_list_agents is True

    lead = AgentCapabilityProfile.from_dict({"preset": "lead"})
    assert lead.allow_code_execution is True
    assert lead.allow_agent_delegation is True
    assert lead.allow_workflow_management is True


def test_capability_profile_from_json_string():
    profile = AgentCapabilityProfile.from_value('{"preset": "builder", "allow_skill_installation": true}')
    assert profile.preset == "builder"
    assert profile.allow_skill_installation is True
    assert profile.allow_code_execution is True


def test_capability_profile_from_preset_string():
    profile = AgentCapabilityProfile.from_value("specialist")
    assert profile.preset == "specialist"
    assert profile.allow_code_execution is False
