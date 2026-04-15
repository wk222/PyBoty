from __future__ import annotations

import sys
from types import SimpleNamespace

from core.systems.context import WorkspaceViewService
from core.systems.runtime import pybot_bootstrap
from core.systems.runtime.pybot_bootstrap import ToolAssembly, assemble_primary_tools, create_root_agent
from core.systems.runtime.hooks_runtime import create_default_hooks_runtime
from core.systems.runtime.trusted_settings import build_trusted_settings_bundle


class _DummyLLM:
    def __init__(self) -> None:
        self.callback_config = None

    def with_config(self, config):
        self.callback_config = config
        return self


class _DummyMiddleware:
    def __init__(self) -> None:
        self.ask_user_fn = None

    def set_ask_user_fn(self, ask_user_fn):
        self.ask_user_fn = ask_user_fn

    def get_control_snapshot(self):
        return {
            "observability": {
                "recent_events": [
                    {
                        "tool_name": "read_file",
                        "tool_call_id": "run-1",
                        "allowed": True,
                        "requires_approval": False,
                        "args_preview": "{'path': 'app.py'}",
                        "timestamp": 1.0,
                    }
                ]
            },
            "permission": {
                "mode": "plan",
                "summary": "mode=plan, 1 active rule",
                "rules": [{"tool_name": "read_file", "verdict": "ask", "reason": "manual", "source": "session"}],
                "recent_events": [{"action": "set_mode", "mode": "plan", "timestamp": 1.0}],
                "write_tools": ["write_file", "bash"],
                "rule_count": 1,
            },
        }


class _DummyCapabilityBus:
    def __init__(self) -> None:
        self.shared_context = {}

    def get_tree_projection(self):
        return {
            "trunk": [
                {"id": "tool_runtime_governance", "label": "Tool Runtime / Governance"},
                {"id": "workspace_view", "label": "Workspace View"},
                {"id": "context_hygiene", "label": "Context Hygiene"},
            ],
            "execution_surfaces": [
                {"id": "single_agent_runtime", "label": "Single-Agent Runtime"},
                {"id": "skill_strategy", "label": "Skill Strategy Overlay"},
            ],
            "primary_branches": [
                {
                    "id": "workflow_apps",
                    "label": "Workflow / Apps / Automation",
                    "depends_on": ["single_agent_runtime", "permission_recovery"],
                    "children": [
                        {"id": "app_runtime", "label": "App Asset Runtime"},
                        {"id": "app_modes", "label": "App Modes"},
                    ],
                    "capabilities": ["create_app", "build_app_iteratively"],
                    "capability_count": 2,
                }
            ],
            "secondary_branches": [
                {"id": "hooks_runtime", "label": "Hooks Runtime"},
            ],
        }

    def get_route_projection(self, **_kwargs):
        return {
            "recommended": {
                "mode": "trunk_first",
                "slot": "workspace_view",
                "slot_label": "Workspace View",
                "top_level": "workspace_view",
                "top_level_label": "Workspace View",
                "summary": "Stay on the trunk through Workspace View first.",
            },
            "top_matches": [{"name": "read_file", "layer": "tool", "tree": {"slot": "workspace_view"}}],
            "route_hints": [{"topic": "start_here", "hint": "Prefer trunk capabilities first."}],
        }

    def share_context(self, key, value, source=""):
        self.shared_context[key] = {"value": value, "source": source}


def test_create_root_agent_uses_runtime_metadata_for_session_context(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    def fake_build_root_langchain_middleware(**kwargs):
        captured["build_kwargs"] = kwargs
        return ["middleware"]

    def fake_create_agent(**kwargs):
        captured["agent_kwargs"] = kwargs
        return {"ok": True}

    class DummyGovernanceApprovalCallback:
        def __init__(self, **kwargs):
            captured["governance_kwargs"] = kwargs

    monkeypatch.setattr("core.systems.middleware.agent_middleware_factory.build_root_langchain_middleware", fake_build_root_langchain_middleware)
    monkeypatch.setattr("langchain.agents.create_agent", fake_create_agent)
    monkeypatch.setattr(
        "core.systems.governance.approval_callback.GovernanceApprovalCallback",
        DummyGovernanceApprovalCallback,
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    runtime = SimpleNamespace(
        thread_id="thread-123",
        conversation_offload_dir=str(tmp_path),
        root_mode="admin",
        workspace_view=WorkspaceViewService(),
        workspace=SimpleNamespace(root_dir=str(tmp_path)),
        context_manager=SimpleNamespace(config=SimpleNamespace(summarize_callback=lambda prompt: "summary")),
        session_runtime=None,
        llm=_DummyLLM(),
        middleware=_DummyMiddleware(),
        capability_bus=_DummyCapabilityBus(),
        subagent_registry=object(),
        task_runtime=SimpleNamespace(
            ingest_tool_runs=lambda *args, **kwargs: None,
            ingest_permission_events=lambda *args, **kwargs: None,
            record_compaction_boundary=lambda *args, **kwargs: None,
            build_projection=lambda: {"summary": "", "tasks": [], "activities": []},
        ),
        hooks_runtime=create_default_hooks_runtime(),
        trusted_settings=build_trusted_settings_bundle(
            user_values={"permission": {"mode": "plan"}},
            project_values={"channels": {"weather": {"token": "configured"}}},
        ),
        approval_queue=None,
        checkpointer=object(),
    )
    assembly = ToolAssembly(
        creator_tools=[],
        dynamic_tools=[],
        all_tools=[],
        system_prompt="system prompt",
        tool_groups=[],
    )

    result = create_root_agent(runtime=runtime, assembly=assembly)

    assert result == {"ok": True}

    build_kwargs = captured["build_kwargs"]
    summ_config = build_kwargs["summarization_config"]
    session_extractor = build_kwargs["session_memory_extractor"]
    runtime_view_provider = build_kwargs["runtime_view_provider"]
    compaction_callback = build_kwargs["session_compaction_callback"]
    governance_kwargs = captured["governance_kwargs"]

    assert summ_config.thread_id == "thread-123"
    assert summ_config.offload_dir == str(tmp_path)
    assert session_extractor._config.thread_id == "thread-123"
    assert session_extractor._config.storage_dir == str(tmp_path)
    assert governance_kwargs["thread_id"] == "thread-123"

    runtime.workspace_view.record_view(
        resolved_path=str(tmp_path / "app.py"),
        content="print('hello')\n",
        mtime=1.0,
        file_size=15,
    )
    session_extractor._notes = "## Current objective\nContinue refactor"
    compaction_callback(
        {
            "thread_id": "thread-123",
            "summary": "Compacted earlier context",
            "source": "middleware.summarization",
            "reason": "conversation_compaction",
            "message_count": 8,
            "recent_window": 20,
            "microcompact_count": 2,
        }
    )
    artifacts = runtime_view_provider()

    assert artifacts is not None
    view = artifacts["projected_runtime_view"]
    assert artifacts["system_context"]["thread_id"] == "thread-123"
    assert artifacts["system_context"]["primary_mode"] == "admin"
    assert view["session"]["session_notebook_summary"] == "## Current objective\nContinue refactor"
    assert view["session"]["compaction_summary"] == "Compacted earlier context"
    assert view["workspace"]["recent_paths"] == [str(tmp_path / "app.py")]
    assert view["tasks"]["activities"][0]["title"] == "read_file"
    assert view["permission"]["mode"] == "plan"
    assert view["settings"]["permission_mode"] == "plan"
    assert artifacts["system_context"]["latest_compaction_boundary"]["summary"] == "Compacted earlier context"
    assert view["capability"]["primary_branches"][0]["label"] == "Workflow / Apps / Automation"
    assert "create_app" in [item["topic"] for item in view["capability"]["route_hints"]]
    assert view["context_hygiene"]["summary_active"] is True
    assert view["hooks"]["active_phases"]
    assert view["route"]["recommended"]["slot"] == "workspace_view"
    assert view["isolation"]["multi_agent_ready"] is True
    assert view["context_hygiene"]["history_snip_count"] == 1


def test_assemble_primary_tools_passes_runtime_workspace_view(tmp_path, monkeypatch):
    captured: dict[str, object] = {}
    marker = object()

    # The implementation uses local imports, so we must patch the actual sources
    monkeypatch.setattr("core.assets.tools.tool_creator.get_tool_creator_tools", lambda **kwargs: [])
    monkeypatch.setattr("core.assets.tools.clarification_tool.get_clarification_tools", lambda: [])
    monkeypatch.setattr("core.assets.skills.skill_marketplace.get_marketplace_tools", lambda *_args, **_kwargs: [])

    def fake_get_execution_loop_tools(workspace_dir):
        captured["workspace_dir"] = workspace_dir
        return []
    monkeypatch.setattr("core.systems.execution.execution_loop.get_execution_loop_tools", fake_get_execution_loop_tools)

    monkeypatch.setattr("core.assets.tools.tool_chain.get_tool_chain_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.eval.eval_framework.get_eval_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.bus.capability_bus.get_capability_bus_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.bus.capability_registry.get_capability_registry_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.memory.memory_tools.get_memory_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.runtime.prompts.build_static_system_prompt", lambda **_kwargs: "system prompt")
    
    # These are namespaces imported in factories.py
    monkeypatch.setattr(
        "core.modes.apps.app_runtime",
        SimpleNamespace(
            creator_tools_factory=lambda **kwargs: [],
            verifier_tools_factory=lambda: [],
        ),
    )
    monkeypatch.setattr(
        "core.assets.workflows.workflow_runtime",
        SimpleNamespace(tools_factory=lambda *_args, **_kwargs: []),
    )
    monkeypatch.setattr(
        "core.modes.apps.app_orchestration",
        SimpleNamespace(
            marketplace_tools_factory=lambda: [],
            orchestration_tools_factory=lambda *_args, **_kwargs: [],
        ),
    )
    monkeypatch.setattr(
        "core.assets.tools.permission_tools.get_permission_tools",
        lambda *_args, **_kwargs: [],
    )

    def fake_get_file_system_tools(**kwargs):
        captured["fs_kwargs"] = kwargs
        return []

    class DummyBashTool:
        name = "bash"

        def __init__(self, **kwargs):
            captured["bash_kwargs"] = kwargs

    class DummyWebFetchTool:
        name = "web_fetch"

    class DummyMarkdownLoader:
        def __init__(self, skills_dir):
            captured["skills_dir"] = skills_dir

        def get_all_skills_summary(self):
            return ""

    monkeypatch.setattr("core.assets.tools.file_system_tools.get_file_system_tools", fake_get_file_system_tools)
    monkeypatch.setattr("core.assets.tools.bash_tool.BashTool", DummyBashTool)
    monkeypatch.setattr("core.assets.tools.web_fetch_tool.WebFetchTool", DummyWebFetchTool)
    monkeypatch.setattr("core.assets.skills.markdown_loader.MarkdownSkillLoader", DummyMarkdownLoader)

    runtime = SimpleNamespace(
        storage=SimpleNamespace(tools={}),
        agent_storage=object(),
        control_policy=object(),
        approval_queue=None,
        subagent_registry=None,
        skill_marketplace=object(),
        pyflow_engine=object(),
        tool_chain=object(),
        eval_framework=object(),
        capability_bus=object(),
        capability_registry=SimpleNamespace(refresh_local_index=lambda **kwargs: captured.setdefault("indexed", kwargs)),
        mcp_hub=SimpleNamespace(get_tools=lambda: []),
        knowledge_tools=[],
        memory=object(),
        middleware=SimpleNamespace(set_base_tools=lambda tools: captured.setdefault("base_tools", tools)),
        orchestration_registry=None,
        skill_registry=SimpleNamespace(get_active_tools=lambda: []),
        workspace_view=marker,
        channel_manager=SimpleNamespace(set_agent_callback=lambda cb: captured.setdefault("chat_callback", cb)),
    )
    paths = SimpleNamespace(
        workspace_dir=tmp_path,
        skills_dir=tmp_path,
    )

    result = assemble_primary_tools(
        runtime=runtime,
        paths=paths,
        enable_agent_creation=False,
        root_mode="assistant",
        llm_factory=lambda _model, _temp: object(),
        chat_callback=lambda message: message,
    )

    assert isinstance(result, ToolAssembly)
    assert captured["workspace_dir"] == str(tmp_path)
