from __future__ import annotations

import json

import pytest

from agent import PyBot
from core.assets.agents import (
    AgentCapabilityProfile,
    AgentDefinition,
    AgentStorage,
    AgentToolSyncError,
    build_agent_tool_inventory,
    create_agent_record,
    create_sub_agent_instance,
    delegate_agent_task,
    invoke_persisted_agent,
    resume_persisted_agent_approval,
    sync_agent_tool,
)
from core.assets.tools import ToolStorage
from core.systems.governance import AgentControlPolicy, ApprovalQueue
from core.systems.runtime import ProjectPaths


def test_create_agent_record_parses_capabilities(tmp_path):
    storage = AgentStorage(str(tmp_path / "agents"))

    result = create_agent_record(
        agent_storage=storage,
        agent_name="data_analyst",
        role="analyst",
        description="Analyzes data",
        system_prompt="You analyze data.",
        capabilities='["python", "analysis"]',
        capability_profile='{"preset":"builder"}',
        model="gpt-4o-mini",
        temperature=0.2,
    )

    assert result["success"] is True
    saved = storage.get_agent("data_analyst")
    assert saved is not None
    assert saved.capabilities == ["python", "analysis"]
    assert saved.capability_profile["allow_local_tool_creation"] is True


def test_delegate_agent_task_returns_structured_payload(tmp_path, monkeypatch):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help with tasks.",
            tools=["search_notes"],
        )
    )

    class FakeRuntime:
        def invoke(self, **kwargs):
            return {
                "response": "done:hello",
                "agent_name": "helper",
                "role": "helper",
                "success": True,
                "state_update": {"plan": "ok"},
                "tool_names": ["search_notes"],
                "thread_id": kwargs["thread_id"],
            }

    monkeypatch.setattr("core.assets.agents.agent_services.create_sub_agent_instance", lambda **kwargs: FakeRuntime())

    result = delegate_agent_task(
        agent_storage=storage,
        llm_factory=lambda **kwargs: object(),
        agent_name="helper",
        task="hello",
        context="ctx",
    )

    assert result["success"] is True
    assert result["response"] == "done:hello"
    assert result["state_update"] == {"plan": "ok"}
    assert result["has_state_update"] is True
    assert result["state_keys"] == ["plan"]
    assert result["tool_names"] == ["search_notes"]
    assert result["thread_id"].startswith("delegate_helper_")


def test_invoke_persisted_agent_respects_control_policy(tmp_path):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="blocked",
            role="blocked",
            description="Blocked helper",
            system_prompt="Do not run.",
        )
    )
    control_policy = AgentControlPolicy.from_config({"mode": "strict"})

    with pytest.raises(ValueError):
        invoke_persisted_agent(
            agent_storage=storage,
            llm_factory=lambda **kwargs: object(),
            control_policy=control_policy,
            agent_name="blocked",
            task="hello",
        )


def test_subagent_runtime_applies_capability_profile_and_global_tools(tmp_path, monkeypatch):
    agent_storage = AgentStorage(str(tmp_path / "agents"))
    created = create_agent_record(
        agent_storage=agent_storage,
        agent_name="builder",
        role="builder",
        description="Builds tools",
        system_prompt="You build tools.",
        capability_profile=json.dumps({"preset": "builder"}),
    )

    assert created["success"] is True
    agent_def = agent_storage.get_agent("builder")
    assert agent_def is not None
    agent_def.tools = ["shared_tool"]
    agent_storage.update_agent("builder", {"tools": ["shared_tool"]})

    class DummyGraph:
        def invoke(self, state, config=None):
            return {"messages": []}

    monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

    local_storage = ToolStorage(str(tmp_path / "agents" / "builder" / "tools"))
    global_storage = ToolStorage(str(tmp_path / "global_tools"))
    global_storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Shared helper",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "shared",
        },
    )

    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        tool_storage=local_storage,
        global_tool_storage=global_storage,
        agent_storage=agent_storage,
        llm_factory=lambda **kwargs: object(),
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        project_paths=ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace"),
    )

    assert "create_custom_tool" in runtime.tool_names
    assert "shared_tool" in runtime.tool_names
    assert runtime.checkpoint_bundle.backend == "sqlite"
    assert runtime.checkpoint_bundle.path is not None and runtime.checkpoint_bundle.path.exists()


def test_specialist_profile_does_not_receive_privileged_tools(tmp_path, monkeypatch):
    class DummyGraph:
        def invoke(self, state, config=None):
            return {"messages": []}

    monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

    agent_def = AgentDefinition(
        name="specialist",
        role="specialist",
        description="Focused specialist",
        system_prompt="You are focused.",
        capability_profile=AgentCapabilityProfile.from_value("specialist").to_dict(),
    )
    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        tool_storage=None,
        agent_storage=None,
        llm_factory=lambda **kwargs: object(),
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
    )

    assert "create_custom_tool" not in runtime.tool_names
    assert "delegate_to_agent" not in runtime.tool_names


def test_subagent_profile_blocks_code_execution_tools(tmp_path, monkeypatch):
    class DummyGraph:
        def invoke(self, state, config=None):
            return {"messages": []}

    monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

    global_storage = ToolStorage(str(tmp_path / "global_tools"))
    global_storage.add_tool(
        "exec_code",
        {
            "name": "exec_code",
            "description": "Execute code",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "exec",
        },
    )

    agent_def = AgentDefinition(
        name="restricted_builder",
        role="builder",
        description="Builder with restricted sandbox",
        system_prompt="Build carefully.",
        tools=["exec_code"],
        capability_profile=AgentCapabilityProfile.from_value(
            {"preset": "builder", "allow_code_execution": False}
        ).to_dict(),
    )

    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        tool_storage=ToolStorage(str(tmp_path / "agents" / "restricted_builder" / "tools")),
        global_tool_storage=global_storage,
        agent_storage=None,
        llm_factory=lambda **kwargs: object(),
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        project_paths=ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace"),
    )

    assert "exec_code" not in runtime.tool_names
    assert "exec_code" in runtime.control_policy.blocked_tools


def test_agent_tool_inventory_separates_assigned_local_and_missing_tools(tmp_path):
    agent_def = AgentDefinition(
        name="helper",
        role="helper",
        description="General helper",
        system_prompt="Help with tasks.",
        tools=["shared_tool", "missing_tool"],
    )
    global_storage = ToolStorage(str(tmp_path / "global_tools"))
    global_storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Shared helper",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "shared",
        },
    )
    local_storage = ToolStorage(str(tmp_path / "agents" / "helper" / "tools"))
    local_storage.add_tool(
        "local_helper",
        {
            "name": "local_helper",
            "description": "Local helper",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "local",
        },
    )

    inventory = build_agent_tool_inventory(
        agent_def=agent_def,
        global_tool_storage=global_storage,
        local_tool_storage=local_storage,
    )

    assert inventory["assigned_global_tool_names"] == ["shared_tool"]
    assert inventory["local_tool_names"] == ["local_helper"]
    assert inventory["missing_assigned_tools"][0]["name"] == "missing_tool"
    assert inventory["assigned_global_tools"][0]["sync_status"] == "global_only"
    assert inventory["local_tools"][0]["sync_status"] == "local_only"


def test_builder_profile_gets_isolated_execution_tools(tmp_path, monkeypatch):
    class DummyGraph:
        def invoke(self, state, config=None):
            return {"messages": []}

    monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

    paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace")
    agent_def = AgentDefinition(
        name="builder",
        role="builder",
        description="Builder",
        system_prompt="Build things.",
        capability_profile=AgentCapabilityProfile.from_value("builder").to_dict(),
    )

    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        tool_storage=None,
        global_tool_storage=None,
        agent_storage=None,
        llm_factory=lambda **kwargs: object(),
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        project_paths=paths,
    )

    assert "exec_code" in runtime.tool_names
    assert "scan_project" in runtime.tool_names
    assert "iterative_test" not in runtime.tool_names
    assert runtime.sandbox.adapter == "isolated"
    assert runtime.sandbox.visibility == "isolated"
    assert runtime.sandbox.workspace_dir != paths.workspace_dir


def test_researcher_profile_gets_read_only_workspace_sandbox(tmp_path, monkeypatch):
    class DummyGraph:
        def invoke(self, state, config=None):
            return {"messages": []}

    monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

    paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace")
    agent_def = AgentDefinition(
        name="researcher",
        role="researcher",
        description="Researcher",
        system_prompt="Research carefully.",
        capability_profile=AgentCapabilityProfile.from_value("researcher").to_dict(),
    )

    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        tool_storage=None,
        global_tool_storage=None,
        agent_storage=None,
        llm_factory=lambda **kwargs: object(),
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        project_paths=paths,
    )

    assert "scan_project" in runtime.tool_names
    assert "exec_code" not in runtime.tool_names
    assert runtime.sandbox.adapter == "workspace"
    assert runtime.sandbox.visibility == "project"
    assert runtime.sandbox.allows_writes is False
    assert runtime.sandbox.workspace_dir == paths.workspace_dir


def test_coordinator_profile_uses_shared_tools_adapter(tmp_path, monkeypatch):
    class DummyGraph:
        def invoke(self, state, config=None):
            return {"messages": []}

    monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

    paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace")
    agent_def = AgentDefinition(
        name="coordinator",
        role="coordinator",
        description="Coordinator",
        system_prompt="Coordinate work.",
        capability_profile=AgentCapabilityProfile.from_value("coordinator").to_dict(),
    )

    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        tool_storage=None,
        global_tool_storage=None,
        agent_storage=None,
        llm_factory=lambda **kwargs: object(),
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        project_paths=paths,
    )

    assert "exec_code" not in runtime.tool_names
    assert runtime.sandbox.adapter == "shared_tools"
    assert runtime.sandbox.visibility == "shared_tools"
    assert runtime.sandbox.workspace_dir == paths.tools_workspace_dir / "shared"


def test_lead_profile_gets_workspace_write_execution_tools(tmp_path, monkeypatch):
    class DummyGraph:
        def invoke(self, state, config=None):
            return {"messages": []}

    monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

    paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace")
    agent_def = AgentDefinition(
        name="lead",
        role="lead",
        description="Lead",
        system_prompt="Lead work.",
        capability_profile=AgentCapabilityProfile.from_value("lead").to_dict(),
    )

    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        tool_storage=None,
        global_tool_storage=None,
        agent_storage=None,
        llm_factory=lambda **kwargs: object(),
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        project_paths=paths,
    )

    assert "exec_code" in runtime.tool_names
    assert "scan_project" in runtime.tool_names
    assert "iterative_test" in runtime.tool_names
    assert runtime.sandbox.adapter == "workspace"
    assert runtime.sandbox.visibility == "project"
    assert runtime.sandbox.workspace_dir == paths.workspace_dir


def test_delegate_agent_task_preserves_waiting_approval_payload(tmp_path, monkeypatch):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help with tasks.",
        )
    )

    class FakeRuntime:
        def invoke(self, **kwargs):
            return {
                "status": "waiting_approval",
                "response": "waiting",
                "agent_name": "helper",
                "role": "helper",
                "success": False,
                "state_update": {},
                "tool_names": ["exec_code"],
                "thread_id": kwargs["thread_id"],
                "approval_id": "appr_sub_1",
                "sandbox": {"mode": "restricted", "visibility": "isolated"},
            }

    monkeypatch.setattr("core.assets.agents.agent_services.create_sub_agent_instance", lambda **kwargs: FakeRuntime())

    result = delegate_agent_task(
        agent_storage=storage,
        llm_factory=lambda **kwargs: object(),
        agent_name="helper",
        task="hello",
        context="ctx",
    )

    assert result["status"] == "waiting_approval"
    assert result["approval_id"] == "appr_sub_1"
    assert result["sandbox"]["mode"] == "restricted"


def test_invoke_persisted_agent_includes_structured_tool_inventory(tmp_path, monkeypatch):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help with tasks.",
            tools=["shared_tool"],
        )
    )

    global_storage = ToolStorage(str(tmp_path / "global_tools"))
    global_storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Shared helper",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "shared",
        },
    )
    local_storage = ToolStorage(str(tmp_path / "agents" / "helper" / "tools"))
    local_storage.add_tool(
        "local_helper",
        {
            "name": "local_helper",
            "description": "Local helper",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "local",
        },
    )

    class FakeRuntime:
        def invoke(self, **kwargs):
            return {
                "response": "done:hello",
                "agent_name": "helper",
                "role": "helper",
                "success": True,
                "state_update": {},
                "tool_names": ["shared_tool", "local_helper"],
                "thread_id": kwargs["thread_id"],
                "sandbox": {"mode": "isolated"},
            }

    monkeypatch.setattr("core.assets.agents.agent_services.create_sub_agent_instance", lambda **kwargs: FakeRuntime())

    result = invoke_persisted_agent(
        agent_storage=storage,
        llm_factory=lambda **kwargs: object(),
        agent_name="helper",
        task="hello",
        global_tool_storage=global_storage,
    )

    assert result["tool_inventory"]["assigned_global_tool_names"] == ["shared_tool"]
    assert result["tool_inventory"]["local_tool_names"] == ["local_helper"]


def test_sync_agent_tool_to_global_copies_local_tool_to_global_storage(tmp_path):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help with tasks.",
        )
    )
    local_storage = ToolStorage(str(storage.tools_dir_for("helper")))
    local_storage.add_tool(
        "local_helper",
        {
            "name": "local_helper",
            "description": "Local helper",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "local",
        },
    )
    global_storage = ToolStorage(str(tmp_path / "global_tools"))

    result = sync_agent_tool(
        agent_storage=storage,
        global_tool_storage=global_storage,
        agent_name="helper",
        tool_name="local_helper",
        direction="to_global",
    )

    assert result["action"] == "promoted_to_global"
    assert global_storage.get_tool("local_helper") is not None
    assert result["tool_inventory"]["local_tools"][0]["sync_status"] == "in_sync"


def test_sync_agent_tool_to_global_requires_overwrite_for_conflicts(tmp_path):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help with tasks.",
        )
    )
    local_storage = ToolStorage(str(storage.tools_dir_for("helper")))
    local_storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Local helper",
            "parameters": [],
            "code": "result = 'local'",
            "dependencies": [],
            "usage_guide": "local",
        },
    )
    global_storage = ToolStorage(str(tmp_path / "global_tools"))
    global_storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Global helper",
            "parameters": [],
            "code": "result = 'global'",
            "dependencies": [],
            "usage_guide": "global",
        },
    )

    with pytest.raises(AgentToolSyncError):
        sync_agent_tool(
            agent_storage=storage,
            global_tool_storage=global_storage,
            agent_name="helper",
            tool_name="shared_tool",
            direction="to_global",
        )

    result = sync_agent_tool(
        agent_storage=storage,
        global_tool_storage=global_storage,
        agent_name="helper",
        tool_name="shared_tool",
        direction="to_global",
        overwrite=True,
    )

    assert result["action"] == "overwrote_global"
    assert global_storage.get_tool("shared_tool")["code"] == "result = 'local'"


def test_sync_agent_tool_from_global_copies_tool_into_local_storage(tmp_path):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help with tasks.",
            tools=["shared_tool"],
        )
    )
    global_storage = ToolStorage(str(tmp_path / "global_tools"))
    global_storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Global helper",
            "parameters": [],
            "code": "result = 'global'",
            "dependencies": [],
            "usage_guide": "global",
        },
    )

    result = sync_agent_tool(
        agent_storage=storage,
        global_tool_storage=global_storage,
        agent_name="helper",
        tool_name="shared_tool",
        direction="from_global",
    )

    local_storage = ToolStorage(str(storage.tools_dir_for("helper")))
    assert result["action"] == "pulled_to_local"
    assert local_storage.get_tool("shared_tool") is not None
    assert result["tool_inventory"]["assigned_global_tools"][0]["sync_status"] == "in_sync"
    assert result["tool_inventory"]["local_tools"][0]["sync_status"] == "in_sync"


def test_sync_agent_tool_from_global_requires_overwrite_for_conflicts(tmp_path):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help with tasks.",
            tools=["shared_tool"],
        )
    )
    local_storage = ToolStorage(str(storage.tools_dir_for("helper")))
    local_storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Local helper",
            "parameters": [],
            "code": "result = 'local'",
            "dependencies": [],
            "usage_guide": "local",
        },
    )
    global_storage = ToolStorage(str(tmp_path / "global_tools"))
    global_storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Global helper",
            "parameters": [],
            "code": "result = 'global'",
            "dependencies": [],
            "usage_guide": "global",
        },
    )

    with pytest.raises(AgentToolSyncError):
        sync_agent_tool(
            agent_storage=storage,
            global_tool_storage=global_storage,
            agent_name="helper",
            tool_name="shared_tool",
            direction="from_global",
        )

    result = sync_agent_tool(
        agent_storage=storage,
        global_tool_storage=global_storage,
        agent_name="helper",
        tool_name="shared_tool",
        direction="from_global",
        overwrite=True,
    )

    assert result["action"] == "overwrote_local"
    refreshed_local = ToolStorage(str(storage.tools_dir_for("helper")))
    assert refreshed_local.get_tool("shared_tool")["code"] == "result = 'global'"


def test_pybot_resolve_approval_replaces_result_with_parent_resume():
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="helper approval",
        prompt="allow?",
        callback=lambda approved, note: {"status": "completed", "response": "subagent done"},
    )

    queue.update_request_metadata(
        request.approval_id,
        target="subagent:helper",
        thread_id="delegate-helper-thread",
        parent_thread_id="session-1",
        parent_target="root_agent",
    )

    bot = PyBot.__new__(PyBot)
    bot.approval_queue = queue
    bot.thread_id = "session-1"
    bot._resume_delegated_tool_approval = lambda **kwargs: {"status": "completed", "response": "parent resumed"}
    bot._rebuild_runtime_result_if_needed = PyBot._rebuild_runtime_result_if_needed.__get__(bot, PyBot)
    bot._resume_parent_orchestration_if_needed = PyBot._resume_parent_orchestration_if_needed.__get__(bot, PyBot)
    bot._is_recorded_resolution = PyBot._is_recorded_resolution
    bot._invoke_config = lambda **kwargs: {"configurable": {"thread_id": "session-1"}, "recursion_limit": 100}

    result = PyBot.resolve_approval(
        bot,
        request.approval_id,
        approved=True,
        note="ok",
        approver="lead",
    )

    assert result["success"] is True
    assert result["approval"]["resolved_by"] == "lead"
    assert result["subagent_result"]["response"] == "subagent done"
    assert result["result"]["response"] == "parent resumed"


def test_pybot_resolve_approval_replays_root_tool_interrupt_from_metadata(monkeypatch):
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="root:session-1",
        summary="root tool approval",
        prompt="allow root?",
        metadata={
            "target": "root_agent",
            "thread_id": "session-1",
            "interrupt_id": "interrupt-1",
            "action_requests": [{"name": "create_agent", "args": {"agent_name": "helper"}, "tool_call_id": "call_1"}],
            "review_configs": [{"action_name": "create_agent", "allowed_decisions": ["approve", "reject"]}],
            "approval_scope": "root:session-1",
        },
    )

    bot = PyBot.__new__(PyBot)
    bot.approval_queue = queue
    bot.thread_id = "session-1"
    bot.middleware = type("Middleware", (), {"approval_scope": "root:session-1"})()
    bot._resume_tool_approval = lambda **kwargs: {"status": "completed", "response": "root resumed"}
    bot._resume_root_tool_approval_from_request = PyBot._resume_root_tool_approval_from_request.__get__(bot, PyBot)
    bot._rebuild_runtime_result_if_needed = PyBot._rebuild_runtime_result_if_needed.__get__(bot, PyBot)
    bot._resume_parent_orchestration_if_needed = PyBot._resume_parent_orchestration_if_needed.__get__(bot, PyBot)
    bot._is_recorded_resolution = PyBot._is_recorded_resolution
    bot._invoke_config = lambda **kwargs: {"configurable": {"thread_id": "session-1"}, "recursion_limit": 100}

    result = PyBot.resolve_approval(
        bot,
        request.approval_id,
        approved=True,
        note="ok",
        approver="lead",
    )

    assert result["success"] is True
    assert result["result"]["response"] == "root resumed"
    assert queue.get_request(request.approval_id).resolution_result["response"] == "root resumed"


def test_resume_persisted_agent_approval_updates_queue_result(tmp_path, monkeypatch):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help with tasks.",
        )
    )
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="helper approval",
        prompt="allow helper?",
        metadata={"target": "subagent:helper", "thread_id": "delegate-helper-thread"},
    )

    class FakeRuntime:
        def resume_approval(self, **kwargs):
            return {"status": "completed", "response": "helper resumed", "thread_id": kwargs["thread_id"]}

    monkeypatch.setattr("core.assets.agents.agent_services.create_sub_agent_instance", lambda **kwargs: FakeRuntime())

    result = resume_persisted_agent_approval(
        agent_storage=storage,
        llm_factory=lambda **kwargs: object(),
        approval_queue=queue,
        approval_id=request.approval_id,
        agent_name="helper",
        thread_id="delegate-helper-thread",
        approved=True,
        note="ok",
    )

    assert result["response"] == "helper resumed"
    assert queue.get_request(request.approval_id).resolution_result["response"] == "helper resumed"
