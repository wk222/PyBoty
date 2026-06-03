from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.assets.skills import SkillRegistry
from core.assets.skills.skill_marketplace import SkillMarketplace
from core.modes.builtin_packs import ensure_builtin_packs
from core.modes.pack import (
    BaseModePack,
    ModePack,
    ModePackRegistry,
    get_global_registry,
)
from core.modes.profile import ModeProfile, resolve_mode_profile
from core.modes.admin.planner import AdminPlanner, fallback_admin_plan
from core.modes.admin.runtime import PersistentAdminRuntime
from core.modes.admin.watcher import AdminWatcherDaemon
from core.systems.apps.app_manager import AppManager
from core.systems.apps.app_matrix_planner import AppMatrixPlanner, fallback_app_matrix_plan
from core.systems.apps.app_matrix_runtime import AppMatrixRuntime
from core.systems.apps.app_orchestration import (
    AppOrchestrationRegistry,
    BindingDirection,
    DataBinding,
    NodeStatus,
    NodeType,
    OrchestrationNode,
    OrchestrationPipeline,
)
from core.systems.apps.app_verifier import ReadAppFileTool, VerifyAppTool, set_verifier_app_manager
from core.systems.capability.capability_bus import CapabilityBus, CapabilityLayer
from core.systems.capability.capability_registry import CapabilityRegistry
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.agents.persistent_agent_runner import PersistentTaskStatus
from core.systems.runtime.event_bus import Event, EventType
from langchain_core.messages import AIMessage


# ═══════════════════════════════════════════════════════════════════════════
# 1. Mode Pack Registry Tests (formerly test_mode_pack_registry.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestModePackRegistry:
    def test_register_and_resolve(self) -> None:
        registry = ModePackRegistry()
        pack = _make_stub_pack("test_mode")
        registry.register(pack)
        assert registry.get("test_mode") is pack

    def test_resolve_unknown_raises(self) -> None:
        registry = ModePackRegistry()
        with pytest.raises(KeyError, match="Unknown mode pack"):
            registry.get("nonexistent")

    def test_get_or_none(self) -> None:
        registry = ModePackRegistry()
        assert registry.get_or_none("absent") is None
        pack = _make_stub_pack("present")
        registry.register(pack)
        assert registry.get_or_none("present") is pack

    def test_unregister(self) -> None:
        registry = ModePackRegistry()
        pack = _make_stub_pack("removable")
        registry.register(pack)
        removed = registry.unregister("removable")
        assert removed is pack
        assert "removable" not in registry

    def test_unregister_missing_returns_none(self) -> None:
        registry = ModePackRegistry()
        assert registry.unregister("missing") is None

    def test_list_all(self) -> None:
        registry = ModePackRegistry()
        a, b = _make_stub_pack("a"), _make_stub_pack("b")
        registry.register(a)
        registry.register(b)
        assert set(registry.names()) == {"a", "b"}
        assert len(registry) == 2

    def test_contains(self) -> None:
        registry = ModePackRegistry()
        registry.register(_make_stub_pack("x"))
        assert "x" in registry
        assert "y" not in registry


class TestBuiltinPacks:
    def test_builtin_packs_registered(self) -> None:
        ensure_builtin_packs()
        registry = get_global_registry()
        for name in ("assistant", "admin", "app_matrix"):
            pack = registry.get(name)
            assert pack.name == name
            assert isinstance(pack.profile, ModeProfile)

    def test_builtin_packs_idempotent(self) -> None:
        ensure_builtin_packs()
        ensure_builtin_packs()
        registry = get_global_registry()
        assert len([p for p in registry.list_all() if p.name in ("assistant", "admin", "app_matrix")]) == 3


class TestPackProtocol:
    def test_assistant_pack_satisfies_protocol(self) -> None:
        from core.modes.assistant_pack import AssistantPack
        pack = AssistantPack()
        assert isinstance(pack, ModePack)
        assert pack.name == "assistant"
        assert pack.get_api_methods() == {}
        assert pack.get_tools(None) == []

    def test_admin_pack_satisfies_protocol(self) -> None:
        from core.modes.admin.pack import AdminPack
        pack = AdminPack()
        assert isinstance(pack, ModePack)
        assert pack.name == "admin"
        api = pack.get_api_methods()
        assert "start_admin_loop" in api
        assert "submit_admin_goal" in api

    def test_app_matrix_pack_satisfies_protocol(self) -> None:
        from core.modes.app_matrix_pack import AppMatrixPack
        pack = AppMatrixPack()
        assert isinstance(pack, ModePack)
        assert pack.name == "app_matrix"
        api = pack.get_api_methods()
        assert "start_admin_loop" in api
        assert "submit_app_matrix_goal" in api
        assert "plan_app_matrix_topology" in api


class TestFourthModePlugin:
    def test_custom_pack_register_and_resolve(self) -> None:
        registry = ModePackRegistry()
        custom = _make_custom_pack()
        registry.register(custom)
        resolved = registry.get("research")
        assert resolved is custom
        assert resolved.name == "research"
        api = resolved.get_api_methods()
        assert "run_hypothesis" in api

    def test_custom_pack_api_dispatch(self) -> None:
        custom = _make_custom_pack()
        api = custom.get_api_methods()
        result = api["run_hypothesis"](None, hypothesis="test")
        assert result == {"hypothesis": "test", "result": "validated"}

    def test_custom_pack_prompt(self) -> None:
        custom = _make_custom_pack()
        prompt = custom.get_prompt_section(None)
        assert "research" in prompt.lower()


class TestBaseModePack:
    def test_defaults(self) -> None:
        profile = resolve_mode_profile("assistant")
        pack = BaseModePack(_name="test", _profile=profile)
        assert pack.name == "test"
        assert pack.profile is profile
        pack.initialize(None)
        pack.teardown(None)
        assert pack.get_tools(None) == []
        assert pack.get_prompt_section(None) == ""
        assert pack.get_api_methods() == {}


def _make_stub_pack(name: str) -> BaseModePack:
    return BaseModePack(_name=name, _profile=resolve_mode_profile("assistant"))


class _ResearchPack(BaseModePack):
    def __init__(self) -> None:
        profile = resolve_mode_profile("assistant")
        super().__init__(_name="research", _profile=profile)

    def get_prompt_section(self, host: Any) -> str:
        return "你当前处于 Research 模式，专注于假设验证和实验设计。"

    def get_api_methods(self) -> dict[str, Callable[..., Any]]:
        return {
            "run_hypothesis": _api_run_hypothesis,
        }


def _api_run_hypothesis(host: Any, *, hypothesis: str) -> dict[str, Any]:
    return {"hypothesis": hypothesis, "result": "validated"}


def _make_custom_pack() -> _ResearchPack:
    return _ResearchPack()


# ═══════════════════════════════════════════════════════════════════════════
# 2. App Manager Tests (formerly test_app_manager.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestAppManagerClass:
    def test_app_manager_persists_api_enablement_and_toggle(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)

        created = manager.create_app("demo", "Demo", "test app")
        assert created["success"] is True

        updated = manager.update_app_file("demo", "api.py", "result = {'value': payload['value']}")
        assert updated["success"] is True
        assert manager.get_app("demo").api_enabled is True

        toggled = manager.toggle_app("demo", False)
        assert toggled == {"success": True, "app": "demo", "enabled": False}

        metadata = json.loads((temp_paths.apps_dir / "demo" / "app.json").read_text(encoding="utf-8"))
        assert metadata["api_enabled"] is True
        assert metadata["enabled"] is False

    def test_app_manager_executes_app_api(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        manager.create_app("demo", "Demo", "test app")
        manager.update_app_file(
            "demo",
            "api.py",
            (
                "if action == 'echo':\n"
                "    result = {'echo': payload['value'], 'db_path': DB_PATH}\n"
                "else:\n"
                "    result = {'echo': None}\n"
            ),
        )

        result = manager.execute_app_api("demo", "echo", {"value": "hello"})

        assert result["success"] is True
        assert result["result"]["echo"] == "hello"
        assert result["result"]["db_path"].endswith("agent.db")

    def test_app_manager_reloads_persisted_apps(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        manager.create_app("demo", "Demo", "test app")

        reloaded = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)

        assert reloaded.get_app("demo") is not None
        assert reloaded.get_app("demo").display_name == "Demo"

    def test_switch_app_mode(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        result = manager.create_app("test_switch", mode="static")
        assert result["success"]

        switch_res = manager.switch_app_mode("test_switch", "chat", rebuild_template=False)
        assert switch_res["success"]
        assert manager.get_app("test_switch").mode == "chat"
        
        index_content = (temp_paths.apps_dir / "test_switch" / "index.html").read_text(encoding="utf-8")
        assert "class=\"chat-header\"" not in index_content

        switch_res2 = manager.switch_app_mode("test_switch", "rag", rebuild_template=True)
        assert switch_res2["success"]
        assert manager.get_app("test_switch").mode == "rag"
        
        index_content2 = (temp_paths.apps_dir / "test_switch" / "index.html").read_text(encoding="utf-8")
        assert "class=\"rag-container\"" in index_content2


# ═══════════════════════════════════════════════════════════════════════════
# 3. App Matrix Planner Tests (formerly test_app_matrix_planner.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestAppMatrixPlannerClass:
    def test_app_matrix_planner_returns_structured_topology_plan(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(
            content=(
                '{"summary":"Use CRM and Audit apps to coordinate customer follow-up and compliance checks",'
                '"participating_apps":["crm","audit"],'
                '"bindings":[{"source_app":"crm","target_app":"audit",'
                '"description":"Send customer cases for audit review"}],'
                '"pipelines":[{"name":"crm_audit_loop","steps":["crm","audit"],"description":"Review flagged customers"}],'
                '"missing_capabilities":["Shared customer risk scoring"],'
                '"planning_notes":"Prefer reusing current apps before creating a new one"}'
            )
        )

        planner = AppMatrixPlanner(mock_llm, method="json_mode")
        plan = planner.plan_topology(
            goal_name="customer_risk_loop",
            goal_description="Coordinate CRM follow-up with audit review",
            app_inventory=[
                {"name": "crm", "mode": "assistant"},
                {"name": "audit", "mode": "workflow"},
            ],
            context={"team": "ops"},
        )

        assert plan.summary.startswith("Use CRM and Audit apps")
        assert plan.participating_apps == ["crm", "audit"]
        assert plan.bindings[0].source_app == "crm"
        assert plan.pipelines[0].name == "crm_audit_loop"
        assert "Shared customer risk scoring" in plan.missing_capabilities

    def test_fallback_app_matrix_plan_uses_existing_apps_when_available(self):
        plan = fallback_app_matrix_plan(
            goal_name="cross_app_goal",
            goal_description="Coordinate apps around a shared business goal",
            app_inventory=[
                {"name": "crm"},
                {"name": "audit"},
                {"name": "marketing"},
            ],
            error="planner unavailable",
        )

        assert plan.participating_apps == ["crm", "audit", "marketing"]
        assert plan.pipelines[0].steps == ["crm", "audit", "marketing"]
        assert "planner unavailable" in plan.planning_notes


# ═══════════════════════════════════════════════════════════════════════════
# 4. App Matrix Runtime Tests (formerly test_app_matrix_runtime.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestAppMatrixRuntimeClass:
    def test_app_matrix_runtime_syncs_apps_into_registry(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        manager.create_app(
            "crm",
            display_name="CRM",
            description="Customer management app",
            tags=["sales"],
            mode="assistant",
            agent_binding="crm_specialist",
            shared_datastores=["customer_db"],
            shared_schemas=[{"name": "customer_profile", "version": "v1"}],
            data_contracts=[{"name": "lead_event", "producer": "crm", "consumer": "audit"}],
        )
        manager.create_app(
            "audit",
            display_name="Audit",
            description="Audit dashboard",
            tags=["finance"],
            mode="workflow",
            workflow_binding="audit_flow",
        )
        manager.reload_apps()

        registry = AppOrchestrationRegistry(storage_path=temp_paths.workspace_data_dir / "app_orchestration.json")
        runtime = AppMatrixRuntime(app_manager=manager, orchestration_registry=registry)

        result = runtime.sync_apps()

        assert len(result["synced"]) == 2
        crm_node = registry.find_node("crm", node_type=NodeType.APP)
        assert crm_node is not None
        assert crm_node.domain == "sales"
        assert crm_node.metadata["agent_binding"] == "crm_specialist"
        assert crm_node.metadata["shared_datastores"] == ["customer_db"]
        assert crm_node.metadata["shared_schemas"][0]["name"] == "customer_profile"
        assert crm_node.metadata["data_contracts"][0]["name"] == "lead_event"
        audit_node = registry.find_node("audit", node_type=NodeType.APP)
        assert audit_node is not None
        assert audit_node.metadata["workflow_binding"] == "audit_flow"

    def test_app_matrix_runtime_builds_bindings_and_pipeline(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        manager.create_app("crm", display_name="CRM", description="CRM app", tags=["sales"])
        manager.create_app("marketing", display_name="Marketing", description="Marketing app", tags=["growth"])
        manager.reload_apps()

        registry = AppOrchestrationRegistry()
        runtime = AppMatrixRuntime(app_manager=manager, orchestration_registry=registry)
        runtime.sync_apps()

        binding = runtime.connect_apps(
            "crm",
            "marketing",
            description="Lead data flows into marketing analysis",
        )
        pipeline = runtime.register_pipeline(
            "growth_loop",
            ["crm", "marketing"],
            description="CRM to marketing analysis",
            schedule="0 9 * * *",
        )
        overview = runtime.get_overview()
        summary = runtime.get_app_summary("crm")

        assert binding["description"] == "Lead data flows into marketing analysis"
        assert pipeline["name"] == "growth_loop"
        assert overview["topology"]["stats"]["total_nodes"] == 2
        assert overview["topology"]["stats"]["total_bindings"] == 1
        assert summary is not None
        assert "marketing" in summary["downstream"]

    def test_app_matrix_runtime_updates_contract_metadata_and_summary(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        manager.create_app("crm", display_name="CRM", description="CRM app", tags=["sales"])
        manager.reload_apps()

        registry = AppOrchestrationRegistry()
        runtime = AppMatrixRuntime(app_manager=manager, orchestration_registry=registry)
        runtime.sync_apps()

        result = runtime.update_app_contract_metadata(
            "crm",
            shared_datastores=["customer_db", "billing_db"],
            shared_schemas=[{"name": "customer_profile", "version": "v2"}],
            data_contracts=[{"name": "customer_sync", "fields": ["customer_id", "status"]}],
        )

        summary = result["summary"]
        assert summary is not None
        assert summary["contracts"]["shared_datastores"] == ["customer_db", "billing_db"]
        assert summary["contracts"]["shared_schemas"][0]["version"] == "v2"
        assert summary["contracts"]["data_contracts"][0]["name"] == "customer_sync"

    def test_app_matrix_runtime_discovers_services_and_invokes_with_grant(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        manager.create_app(
            "parser",
            display_name="Parser",
            description="Parses documents",
            exports=["parse.excel"],
            require_auth=True,
        )
        manager.create_app("crm", display_name="CRM", description="Caller app")
        manager.update_app_file(
            "parser",
            "api.py",
            (
                "if action == 'parse':\n"
                "    result = {'ok': True, 'kind': payload['kind']}\n"
                "else:\n"
                "    result = {'ok': False}\n"
            ),
        )
        manager.reload_apps()

        registry = AppOrchestrationRegistry()
        capability_registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=CapabilityBus(str(temp_paths.workspace_dir)),
            skill_marketplace=SkillMarketplace(str(temp_paths.workspace_dir)),
            app_manager=manager,
        )
        runtime = AppMatrixRuntime(
            app_manager=manager,
            orchestration_registry=registry,
            capability_registry=capability_registry,
        )
        runtime.sync_apps()
        capability_registry.refresh_local_index(save=True)

        providers = runtime.discover_services(provides="parse.excel")
        grant = runtime.request_service_grant(caller_app="crm", provides="parse.excel", requested_quota=2)
        grants = runtime.list_service_grants(caller_app="crm")
        invoke = runtime.invoke_service(
            caller_app="crm",
            grant_token=grant["grant"]["token"],
            action="parse",
            payload={"kind": "excel"},
        )

        assert providers["count"] == 1
        assert providers["providers"][0]["name"] == "parser"
        assert grant["success"] is True
        assert grants[0]["provider_app"] == "parser"
        assert grants[0]["caller_identity"]["app_name"] == "crm"
        assert grants[0]["provider_policy"]["require_auth"] is True
        assert invoke["success"] is True
        assert invoke["provider_app"] == "parser"
        assert invoke["quota_remaining"] == 1
        assert invoke["result"]["kind"] == "excel"
        parser_capability = capability_registry.capability_bus.get("parser")
        assert parser_capability is not None
        assert parser_capability.invoke_count == 1
        assert capability_registry.capability_bus.get_context("last_capability_execution")["name"] == "parser"

    def test_app_matrix_runtime_enforces_provider_policy_limits(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        manager.create_app(
            "parser",
            display_name="Parser",
            description="Parses documents",
            exports=["parse.excel"],
            require_auth=True,
        )
        manager.create_app("crm", display_name="CRM", description="Caller app")
        manager.update_app_file(
            "parser",
            "api.py",
            "result = {'ok': True, 'action': action, 'payload': payload}\n",
        )
        manager.reload_apps()

        registry = AppOrchestrationRegistry()
        capability_registry = CapabilityRegistry(
            workspace_dir=temp_paths.workspace_dir,
            capability_bus=CapabilityBus(str(temp_paths.workspace_dir)),
            skill_marketplace=SkillMarketplace(str(temp_paths.workspace_dir)),
            app_manager=manager,
        )
        runtime = AppMatrixRuntime(
            app_manager=manager,
            orchestration_registry=registry,
            capability_registry=capability_registry,
        )
        runtime.sync_apps()
        capability_registry.refresh_local_index(save=True)

        grant = runtime.request_service_grant(
            caller_app="crm",
            provides="parse.excel",
            metadata={"allowed_actions": ["parse"], "max_payload_bytes": 32},
        )

        denied_action = runtime.invoke_service(
            caller_app="crm",
            grant_token=grant["grant"]["token"],
            action="delete",
            payload={"kind": "excel"},
        )
        denied_payload = runtime.invoke_service(
            caller_app="crm",
            grant_token=grant["grant"]["token"],
            action="parse",
            payload={"blob": "x" * 128},
        )

        assert denied_action["success"] is False
        assert "not permitted" in denied_action["error"]
        assert denied_payload["success"] is False
        assert "Payload exceeds provider policy limit" in denied_payload["error"]
        parser_capability = capability_registry.capability_bus.get("parser")
        assert parser_capability is not None
        assert parser_capability.invoke_count == 2
        last_execution = capability_registry.capability_bus.get_context("last_capability_execution")
        assert last_execution["name"] == "parser"
        assert "Payload exceeds provider policy limit" in last_execution["metadata"]["error"]


# ═══════════════════════════════════════════════════════════════════════════
# 5. App Orchestration Registry Tests (formerly test_app_orchestration.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestEnums:
    def test_node_type_values(self):
        assert NodeType.APP.value == "app"
        assert NodeType.WORKFLOW.value == "workflow"
        assert NodeType.AGENT.value == "agent"
        assert NodeType.TOOL.value == "tool"
        assert NodeType.EXTERNAL.value == "external"

    def test_binding_direction_values(self):
        assert BindingDirection.INPUT.value == "input"
        assert BindingDirection.OUTPUT.value == "output"
        assert BindingDirection.BIDIRECTIONAL.value == "bidirectional"

    def test_node_status_values(self):
        assert NodeStatus.ACTIVE.value == "active"
        assert NodeStatus.INACTIVE.value == "inactive"
        assert NodeStatus.ERROR.value == "error"
        assert NodeStatus.PENDING.value == "pending"


class TestDataBinding:
    def test_to_dict_minimal(self):
        b = DataBinding("a", "out", "b", "in")
        d = b.to_dict()
        assert d["source_node"] == "a"
        assert d["target_node"] == "b"
        assert "transform" not in d
        assert "description" not in d

    def test_to_dict_with_optional(self):
        b = DataBinding("a", "out", "b", "in", transform="json_extract(.x)", description="extract x")
        d = b.to_dict()
        assert d["transform"] == "json_extract(.x)"
        assert d["description"] == "extract x"

    def test_roundtrip(self):
        b = DataBinding("s", "p1", "t", "p2", direction=BindingDirection.BIDIRECTIONAL, transform="t")
        d = b.to_dict()
        b2 = DataBinding.from_dict(d)
        assert b2.source_node == "s"
        assert b2.target_node == "t"
        assert b2.direction == BindingDirection.BIDIRECTIONAL
        assert b2.transform == "t"


class TestOrchestrationNode:
    def test_roundtrip(self):
        n = OrchestrationNode(
            node_id="n1",
            name="my_app",
            node_type=NodeType.APP,
            description="desc",
            domain="finance",
            owner="admin",
            input_ports=["data_in"],
            output_ports=["report_out"],
            metadata={"key": "val"},
        )
        d = n.to_dict()
        n2 = OrchestrationNode.from_dict(d)
        assert n2.node_id == "n1"
        assert n2.name == "my_app"
        assert n2.node_type == NodeType.APP
        assert n2.domain == "finance"
        assert n2.input_ports == ["data_in"]
        assert n2.output_ports == ["report_out"]
        assert n2.metadata == {"key": "val"}

    def test_defaults(self):
        n = OrchestrationNode(node_id="x", name="x", node_type=NodeType.TOOL)
        assert n.input_ports == ["default"]
        assert n.output_ports == ["default"]
        assert n.status == NodeStatus.ACTIVE


class TestOrchestrationPipeline:
    def test_roundtrip(self):
        p = OrchestrationPipeline(
            pipeline_id="p1",
            name="daily_sync",
            description="sync data",
            steps=["n1", "n2", "n3"],
            schedule="0 8 * * *",
        )
        d = p.to_dict()
        p2 = OrchestrationPipeline.from_dict(d)
        assert p2.pipeline_id == "p1"
        assert p2.name == "daily_sync"
        assert p2.steps == ["n1", "n2", "n3"]
        assert p2.schedule == "0 8 * * *"


class TestRegistryNodes:
    def test_register_and_get(self):
        reg = AppOrchestrationRegistry()
        node = reg.register_node("app_a", NodeType.APP, description="first app", node_id="a1")
        assert node.node_id == "a1"
        assert node.name == "app_a"
        fetched = reg.get_node("a1")
        assert fetched is not None
        assert fetched.name == "app_a"

    def test_register_with_string_type(self):
        reg = AppOrchestrationRegistry()
        node = reg.register_node("wf", "workflow")
        assert node.node_type == NodeType.WORKFLOW

    def test_unregister_node(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        assert reg.unregister_node("a1") is True
        assert reg.get_node("a1") is None
        assert reg.unregister_node("nonexistent") is False

    def test_unregister_cascades_bindings(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        reg.add_binding("a1", "default", "b1", "default")
        assert len(reg.list_bindings()) == 1
        reg.unregister_node("a1")
        assert len(reg.list_bindings()) == 0

    def test_find_node_by_name(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("x_app", NodeType.APP, node_id="x1")
        found = reg.find_node_by_name("x_app")
        assert found is not None
        assert found.node_id == "x1"
        assert reg.find_node_by_name("missing") is None

    def test_list_nodes_filter_by_type(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a1", NodeType.APP)
        reg.register_node("a2", NodeType.APP)
        reg.register_node("w1", NodeType.WORKFLOW)
        apps = reg.list_nodes(node_type=NodeType.APP)
        assert len(apps) == 2
        wfs = reg.list_nodes(node_type="workflow")
        assert len(wfs) == 1

    def test_list_nodes_filter_by_domain(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, domain="sales")
        reg.register_node("b", NodeType.APP, domain="hr")
        sales = reg.list_nodes(domain="sales")
        assert len(sales) == 1
        assert sales[0].domain == "sales"

    def test_list_nodes_filter_by_status(self):
        reg = AppOrchestrationRegistry()
        n = reg.register_node("a", NodeType.APP)
        reg.update_node_status(n.node_id, NodeStatus.ERROR)
        errors = reg.list_nodes(status="error")
        assert len(errors) == 1

    def test_update_status(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        assert reg.update_node_status("a1", "inactive") is True
        assert reg.get_node("a1").status == NodeStatus.INACTIVE
        assert reg.update_node_status("missing", "active") is False

    def test_update_metadata(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.update_node_metadata("a1", version="2.0", author="me")
        node = reg.get_node("a1")
        assert node.metadata["version"] == "2.0"
        assert node.metadata["author"] == "me"
        assert reg.update_node_metadata("missing", k="v") is False


class TestRegistryBindings:
    def test_add_binding(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        b = reg.add_binding("a1", "default", "b1", "default", description="a→b")
        assert b.source_node == "a1"
        assert b.description == "a→b"

    def test_add_binding_missing_node_raises(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        with pytest.raises(KeyError, match="Target node"):
            reg.add_binding("a1", "default", "missing", "default")
        with pytest.raises(KeyError, match="Source node"):
            reg.add_binding("missing", "default", "a1", "default")

    def test_remove_binding(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        reg.add_binding("a1", "default", "b1", "default")
        removed = reg.remove_binding("a1", "b1")
        assert removed == 1
        assert len(reg.list_bindings()) == 0

    def test_list_bindings_by_node(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        reg.register_node("c", NodeType.APP, node_id="c1")
        reg.add_binding("a1", "default", "b1", "default")
        reg.add_binding("b1", "default", "c1", "default")
        a_bindings = reg.list_bindings("a1")
        assert len(a_bindings) == 1
        b_bindings = reg.list_bindings("b1")
        assert len(b_bindings) == 2

    def test_upstream_downstream(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("source", NodeType.APP, node_id="s")
        reg.register_node("middle", NodeType.WORKFLOW, node_id="m")
        reg.register_node("sink", NodeType.APP, node_id="k")
        reg.add_binding("s", "default", "m", "default")
        reg.add_binding("m", "default", "k", "default")
        upstream = reg.get_upstream("m")
        assert len(upstream) == 1
        assert upstream[0].node_id == "s"
        downstream = reg.get_downstream("m")
        assert len(downstream) == 1
        assert downstream[0].node_id == "k"


class TestRegistryPipelines:
    def test_register_pipeline(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        p = reg.register_pipeline("sync", ["a1", "b1"], schedule="0 * * * *", pipeline_id="p1")
        assert p.pipeline_id == "p1"
        assert p.steps == ["a1", "b1"]

    def test_unregister_pipeline(self):
        reg = AppOrchestrationRegistry()
        reg.register_pipeline("test", [], pipeline_id="p1")
        assert reg.unregister_pipeline("p1") is True
        assert reg.unregister_pipeline("p1") is False

    def test_list_and_get_pipelines(self):
        reg = AppOrchestrationRegistry()
        reg.register_pipeline("a", [], pipeline_id="p1")
        reg.register_pipeline("b", [], pipeline_id="p2")
        assert len(reg.list_pipelines()) == 2
        assert reg.get_pipeline("p1").name == "a"
        assert reg.get_pipeline("missing") is None


class TestTopologyAndValidation:
    def _build_sample(self) -> AppOrchestrationRegistry:
        reg = AppOrchestrationRegistry()
        reg.register_node("data_source", NodeType.EXTERNAL, node_id="ds", domain="data")
        reg.register_node("etl_flow", NodeType.WORKFLOW, node_id="etl", domain="data")
        reg.register_node("dashboard", NodeType.APP, node_id="dash", domain="reporting")
        reg.add_binding("ds", "default", "etl", "default")
        reg.add_binding("etl", "default", "dash", "default")
        reg.register_pipeline("daily_report", ["ds", "etl", "dash"], pipeline_id="dr")
        return reg

    def test_get_topology(self):
        reg = self._build_sample()
        topo = reg.get_topology()
        assert topo["stats"]["total_nodes"] == 3
        assert topo["stats"]["total_bindings"] == 2
        assert topo["stats"]["total_pipelines"] == 1
        assert topo["stats"]["by_type"]["app"] == 1
        assert topo["stats"]["by_domain"]["data"] == 2

    def test_get_node_summary(self):
        reg = self._build_sample()
        summary = reg.get_node_summary("etl")
        assert summary is not None
        assert summary["node"]["name"] == "etl_flow"
        assert "data_source" in summary["upstream"]
        assert "dashboard" in summary["downstream"]
        assert reg.get_node_summary("missing") is None

    def test_validate_clean_graph(self):
        reg = self._build_sample()
        issues = reg.validate_graph()
        assert len(issues) == 0

    def test_validate_detects_orphan(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("lonely", NodeType.APP, node_id="l1")
        issues = reg.validate_graph()
        assert any("Orphan" in i for i in issues)

    def test_validate_detects_bad_port(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1", output_ports=["out1"])
        reg.register_node("b", NodeType.APP, node_id="b1", input_ports=["in1"])
        reg.add_binding("a1", "wrong_port", "b1", "in1")
        issues = reg.validate_graph()
        assert any("wrong_port" in i for i in issues)

    def test_validate_detects_missing_pipeline_node(self):
        reg = AppOrchestrationRegistry()
        reg.register_pipeline("broken", ["missing_node"], pipeline_id="bp")
        issues = reg.validate_graph()
        assert any("missing_node" in i for i in issues)


class TestPersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "orch.json"
            reg = AppOrchestrationRegistry(storage_path=path)
            reg.register_node("a", NodeType.APP, node_id="a1", domain="sales")
            reg.register_node("b", NodeType.WORKFLOW, node_id="b1")
            reg.add_binding("a1", "default", "b1", "default")
            reg.register_pipeline("pipe", ["a1", "b1"], pipeline_id="p1")

            reg2 = AppOrchestrationRegistry(storage_path=path)
            assert reg2.get_node("a1") is not None
            assert reg2.get_node("a1").domain == "sales"
            assert len(reg2.list_bindings()) == 1
            assert reg2.get_pipeline("p1") is not None

    def test_auto_save_on_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "orch.json"
            reg = AppOrchestrationRegistry(storage_path=path)
            reg.register_node("x", NodeType.TOOL, node_id="x1")
            assert path.exists()
            data = json.loads(path.read_text("utf-8"))
            assert "x1" in data["nodes"]

    def test_clear(self):
        reg = AppOrchestrationRegistry()
        reg.register_node("a", NodeType.APP, node_id="a1")
        reg.register_node("b", NodeType.APP, node_id="b1")
        reg.add_binding("a1", "default", "b1", "default")
        reg.register_pipeline("p", [], pipeline_id="p1")
        reg.clear()
        assert len(reg.list_nodes()) == 0
        assert len(reg.list_bindings()) == 0
        assert len(reg.list_pipelines()) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. App Verifier Tests (formerly test_app_verifier.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestAppVerifierClass:
    def test_verify_app_reports_runtime_and_api_issues(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        set_verifier_app_manager(manager)
        manager.create_app("demo", "Demo", "test app")
        manager.update_app_file(
            "demo",
            "index.html",
            """
<html>
<head>
    <link rel="stylesheet" href="static/style.css">
</head>
<body>
    <div id="app"></div>
    <script src="static/app.js"></script>
</body>
</html>
""".strip(),
        )
        manager.update_app_file(
            "demo",
            "static/app.js",
            """
async function loadData() {
    const response = await apiCall('/api/apps/demo/api');
    document.getElementById('app').innerHTML = response.value;
}

loadData();
""".strip(),
        )
        manager.update_app_file("demo", "static/style.css", "body { color: #222; }")
        manager.update_app_file("demo", "api.py", "value = payload.get('value')")

        result = json.loads(VerifyAppTool()._run("demo"))

        assert result["success"] is True
        assert result["verdict"] == "FAIL"
        assert result["summary"]["critical"] >= 2
        assert any(issue["category"] == "runtime" for issue in result["issues"])
        assert any(issue["category"] == "api" for issue in result["issues"])
        assert "必须修复" in result["fix_instructions"]

    def test_verify_app_can_skip_fix_instructions(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        set_verifier_app_manager(manager)
        manager.create_app("demo", "Demo", "test app")

        result = json.loads(VerifyAppTool()._run("demo", auto_fix=False))

        assert result["success"] is True
        assert "fix_instructions" not in result

    def test_read_app_file_rejects_path_escape(self, temp_paths):
        manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
        set_verifier_app_manager(manager)
        manager.create_app("demo", "Demo", "test app")

        result = json.loads(ReadAppFileTool()._run("demo", "../outside.txt"))

        assert result == {"success": False, "error": "路径越权"}


# ═══════════════════════════════════════════════════════════════════════════
# 7. Admin Planner Tests (formerly test_admin_planner.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestAdminPlannerClass:
    def test_admin_planner_returns_structured_plan(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(
            content=(
                '{"summary":"Build a durable weekly report flow",'
                '"steps":["Inspect current data sources","Create reporting workflow","Schedule weekly run"],'
                '"success_criteria":["Workflow runs weekly","Output is shareable"],'
                '"planning_notes":"Prefer reusable workflow artifacts"}'
            )
        )

        planner = AdminPlanner(mock_llm, method="json_mode")
        plan = planner.plan_goal(
            name="weekly_report",
            description="Generate and schedule a weekly operations report",
            context={"priority": "high"},
        )

        assert plan.summary == "Build a durable weekly report flow"
        assert plan.steps == [
            "Inspect current data sources",
            "Create reporting workflow",
            "Schedule weekly run",
        ]
        assert "Workflow runs weekly" in plan.success_criteria

    def test_fallback_admin_plan_is_conservative(self):
        plan = fallback_admin_plan(
            name="research_goal",
            description="Research the market and produce a reusable summary",
            error="planner unavailable",
        )

        assert plan.steps == ["Research the market and produce a reusable summary"]
        assert "planner unavailable" in plan.planning_notes


# ═══════════════════════════════════════════════════════════════════════════
# 8. Admin Runtime Tests (formerly test_admin_runtime.py)
# ═══════════════════════════════════════════════════════════════════════════

class _DummyHostAgent:
    def __init__(self, root_mode: str = "admin"):
        self.root_mode = root_mode
        self.prompts: list[str] = []
        self.approval_queue = ApprovalQueue()

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"done:{len(self.prompts)}"


def _wait_for_task(runtime: PersistentAdminRuntime, task_id: str, timeout: float = 3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = runtime.get_task(task_id)
        if task is not None and task.status in (
            PersistentTaskStatus.COMPLETED,
            PersistentTaskStatus.FAILED,
            PersistentTaskStatus.CANCELLED,
        ):
            return task
        time.sleep(0.05)
    return runtime.get_task(task_id)


class TestAdminRuntimeClass:
    def test_admin_runtime_processes_persistent_task_in_background(self, tmp_path):
        runtime = PersistentAdminRuntime(
            host_agent=_DummyHostAgent(root_mode="admin"),
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
        )
        try:
            task = runtime.submit_goal(
                name="nightly_research",
                description="Research a topic over multiple steps",
                steps=["Gather sources", "Summarize findings"],
                auto_start=False,
            )

            runtime.start()
            result = _wait_for_task(runtime, task.task_id)
            assert result is not None
            assert result.status == PersistentTaskStatus.COMPLETED
            assert result.progress == 1.0
        finally:
            runtime.close()

    def test_admin_runtime_supports_custom_step_executor_and_context(self, tmp_path):
        def _executor(task, step, context):
            if step.description == "Gather":
                return {"numbers": [1, 2, 3]}
            return {"total": sum(context.get("numbers", []))}

        runtime = PersistentAdminRuntime(
            host_agent=_DummyHostAgent(root_mode="assistant"),
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
            step_executor=_executor,
        )
        try:
            task = runtime.submit_goal(
                name="compute_total",
                description="Compute a reusable aggregate",
                steps=["Gather", "Aggregate"],
                auto_start=False,
            )

            runtime.start()
            result = _wait_for_task(runtime, task.task_id)
            assert result is not None
            assert result.status == PersistentTaskStatus.COMPLETED
            assert result.context["numbers"] == [1, 2, 3]
            assert result.context["total"] == 6
        finally:
            runtime.close()

    def test_admin_runtime_auto_plans_when_steps_are_omitted(self, tmp_path):
        def _planner(name, description, context):
            assert name == "launch_ops_loop"
            assert context == {"team": "ops"}
            return {
                "summary": "Stand up an operations loop",
                "steps": ["Audit current process", "Create durable workflow"],
                "success_criteria": ["Workflow exists", "Workflow can be rerun"],
                "planning_notes": "Prefer reusable automation",
            }

        runtime = PersistentAdminRuntime(
            host_agent=_DummyHostAgent(root_mode="admin"),
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
            goal_planner=_planner,
        )
        try:
            task = runtime.submit_goal(
                name="launch_ops_loop",
                description="Build a repeatable operations loop",
                context={"team": "ops"},
                auto_start=False,
            )

            assert [step.description for step in task.steps] == [
                "Audit current process",
                "Create durable workflow",
            ]
            assert task.context["plan_summary"] == "Stand up an operations loop"
            assert task.context["success_criteria"] == ["Workflow exists", "Workflow can be rerun"]
            assert task.context["admin_plan"]["planning_notes"] == "Prefer reusable automation"
        finally:
            runtime.close()

    def test_admin_runtime_supports_app_matrix_mode_prompting(self, tmp_path):
        host = _DummyHostAgent(root_mode="app_matrix")
        runtime = PersistentAdminRuntime(
            host_agent=host,
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
        )
        try:
            task = runtime.submit_goal(
                name="orchestrate_apps",
                description="Coordinate several apps around a shared business flow",
                steps=["Route request to the right apps"],
                auto_start=False,
            )

            runtime.start()
            result = _wait_for_task(runtime, task.task_id)
            assert result is not None
            assert result.status == PersistentTaskStatus.COMPLETED
            assert result.agent_name == "root_app_matrix"
            assert host.prompts
            assert "应用矩阵" in host.prompts[0]
            assert "串联 APP" in host.prompts[0]
        finally:
            runtime.close()

    def test_admin_runtime_compresses_step_context_into_memory(self, tmp_path):
        def _executor(task, step, context):
            return {
                "report": "x" * 1200,
                "step_response": f"finished:{step.step_id}",
            }

        runtime = PersistentAdminRuntime(
            host_agent=_DummyHostAgent(root_mode="admin"),
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
            step_executor=_executor,
            summarize_fn=lambda text: f"summary::{text[:32]}",
        )
        try:
            task = runtime.submit_goal(
                name="compress_context",
                description="Keep long-running context compact",
                steps=["Generate report", "Archive report"],
                auto_start=False,
            )

            runtime.start()
            result = _wait_for_task(runtime, task.task_id)
            assert result is not None
            assert result.status == PersistentTaskStatus.COMPLETED
            assert result.context["report"].endswith("...(truncated)")
            assert result.context["last_step_summary"].startswith("summary::")
            assert result.context["admin_memory"]["summary"].startswith("summary::")
            assert len(result.context["admin_memory"]["recent_entries"]) <= 4
        finally:
            runtime.close()

    def test_admin_runtime_replaces_pending_steps_from_step_output(self, tmp_path):
        def _executor(task, step, context):
            if step.description == "Initial analysis":
                return {
                    "step_response": "Need a more detailed plan",
                    "replacement_steps": ["Collect evidence", "Draft workflow"],
                    "replan_reason": "found_new_path",
                }
            return {"step_response": f"done:{step.description}"}

        runtime = PersistentAdminRuntime(
            host_agent=_DummyHostAgent(root_mode="admin"),
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
            step_executor=_executor,
        )
        try:
            task = runtime.submit_goal(
                name="adaptive_goal",
                description="Adapt execution plan at runtime",
                steps=["Initial analysis", "Legacy next step"],
                auto_start=False,
                auto_plan=False,
            )

            runtime.start()
            result = _wait_for_task(runtime, task.task_id)
            assert result is not None
            assert result.status == PersistentTaskStatus.COMPLETED
            assert [step.description for step in result.steps] == [
                "Initial analysis",
                "Collect evidence",
                "Draft workflow",
            ]
            assert result.context["last_replan_reason"] == "found_new_path"
            assert result.context["admin_plan"]["steps"] == ["Collect evidence", "Draft workflow"]
        finally:
            runtime.close()

    def test_admin_runtime_replans_with_goal_planner_when_requested(self, tmp_path):
        def _planner(name, description, context):
            if context.get("replan_reason") == "need_deeper_plan":
                assert context["completed_steps"] == ["Initial scan"]
                assert context["remaining_steps"] == ["Stale step"]
                return {
                    "summary": "Updated runtime plan",
                    "steps": ["Investigate root cause", "Ship durable fix"],
                    "success_criteria": ["Root cause understood", "Fix shipped"],
                    "planning_notes": "Triggered from execution feedback",
                }
            return {
                "summary": "Initial plan",
                "steps": ["Initial scan", "Stale step"],
                "success_criteria": ["Scan completed"],
            }

        def _executor(task, step, context):
            if step.description == "Initial scan":
                return {
                    "step_response": "Need a better plan",
                    "replan_required": True,
                    "replan_reason": "need_deeper_plan",
                }
            return {"step_response": f"done:{step.description}"}

        runtime = PersistentAdminRuntime(
            host_agent=_DummyHostAgent(root_mode="admin"),
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
            step_executor=_executor,
            goal_planner=_planner,
        )
        try:
            task = runtime.submit_goal(
                name="replanning_goal",
                description="Adapt after new evidence",
                steps=["Initial scan", "Stale step"],
                auto_start=False,
                auto_plan=False,
            )

            runtime.start()
            result = _wait_for_task(runtime, task.task_id)
            assert result is not None
            assert result.status == PersistentTaskStatus.COMPLETED
            assert [step.description for step in result.steps] == [
                "Initial scan",
                "Investigate root cause",
                "Ship durable fix",
            ]
            assert result.context["last_replan_reason"] == "need_deeper_plan"
            assert result.context["admin_plan"]["summary"] == "Updated runtime plan"
        finally:
            runtime.close()

    def test_admin_runtime_pauses_when_step_waits_for_approval(self, tmp_path):
        queue = ApprovalQueue(tmp_path / "approvals.json")
        request = queue.create_request(
            kind="tool_call",
            scope="root:session-1",
            summary="Need human approval",
            prompt="approve?",
        )
        host = _DummyHostAgent(root_mode="admin")
        host.approval_queue = queue

        def _executor(task, step, context):
            return {
                "status": "waiting_approval",
                "approval_id": request.approval_id,
                "response": "waiting for operator",
            }

        runtime = PersistentAdminRuntime(
            host_agent=host,
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
            step_executor=_executor,
            approval_queue=queue,
        )
        try:
            task = runtime.submit_goal(
                name="approval_gate",
                description="Pause until a human approves",
                steps=["Request approval"],
                auto_start=False,
            )

            runtime.start()
            paused = _wait_for_task(runtime, task.task_id)
            assert paused is not None
            assert paused.status == PersistentTaskStatus.PAUSED
            assert paused.context["pending_approval"]["approval_id"] == request.approval_id
            assert paused.steps[0].status == "paused"
        finally:
            runtime.close()

    def test_admin_runtime_recovers_approved_task_after_restart(self, tmp_path):
        queue = ApprovalQueue(tmp_path / "approvals.json")
        request = queue.create_request(
            kind="tool_call",
            scope="root:session-1",
            summary="Need human approval",
            prompt="approve?",
        )

        class _RecoveringHost(_DummyHostAgent):
            def _rebuild_runtime_result_if_needed(self, *, request, result, approved, note):
                return result

            def _resume_parent_orchestration_if_needed(self, *, request, result):
                return result

        def _executor(task, step, context):
            if step.description == "Request approval":
                return {
                    "status": "waiting_approval",
                    "approval_id": request.approval_id,
                    "response": "waiting for operator",
                }
            return {"step_response": f"done:{step.description}"}

        runtime1 = PersistentAdminRuntime(
            host_agent=_RecoveringHost(root_mode="admin"),
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
            step_executor=_executor,
            approval_queue=queue,
        )
        task = runtime1.submit_goal(
            name="restartable_approval_task",
            description="Resume after approval on restart",
            steps=["Request approval", "Finalize"],
            auto_start=False,
            auto_plan=False,
        )
        runtime1.start()
        paused = _wait_for_task(runtime1, task.task_id)
        assert paused is not None
        assert paused.status == PersistentTaskStatus.PAUSED
        runtime1.close()

        queue.resolve(request.approval_id, approved=True, note="approved after restart")
        queue.set_resolution_result(
            request.approval_id,
            {"status": "completed", "response": "approval replayed after restart"},
        )

        runtime2 = PersistentAdminRuntime(
            host_agent=_RecoveringHost(root_mode="admin"),
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
            step_executor=_executor,
            approval_queue=queue,
        )
        try:
            runtime2.start()
            result = _wait_for_task(runtime2, task.task_id, timeout=5.0)
            assert result is not None
            assert result.status == PersistentTaskStatus.COMPLETED
            assert result.steps[0].status == "completed"
            assert result.steps[0].output_data["response"] == "approval replayed after restart"
            assert result.context["last_approval"]["approval_id"] == request.approval_id
            assert result.context["last_approval"]["approved"] is True
            assert result.steps[1].status == "completed"
        finally:
            runtime2.close()

    def test_admin_runtime_collects_and_promotes_capability_gap_candidates(self, tmp_path):
        runtime = PersistentAdminRuntime(
            host_agent=_DummyHostAgent(root_mode="admin"),
            storage_dir=tmp_path,
            poll_interval=0.02,
            max_workers=1,
        )
        try:
            runtime._on_capability_gap_detected(
                Event(
                    type=EventType.CAPABILITY_GAP_DETECTED,
                    source="AdminWatcherDaemon",
                    payload={
                        "source": "app:parser",
                        "event_type": "error",
                        "gap_type": "missing_capability_gap",
                        "suggested_capability_name": "parser_missing_capability_gap",
                        "occurrences": 4,
                        "samples": [{"error": "missing extractor"}],
                    },
                )
            )

            candidates = runtime.list_capability_gap_candidates()
            promoted = runtime.promote_capability_gap_candidate(candidates[0]["candidate_id"], auto_start=False)

            assert len(candidates) == 1
            assert candidates[0]["gap_type"] == "missing_capability_gap"
            assert candidates[0]["recommended_asset_kind"] == "app"
            assert candidates[0]["recommended_publish_target"] == "app_matrix"
            assert candidates[0]["draft_contract"]["name"] == "parser_missing_capability_gap"
            assert promoted["success"] is True
            assert promoted["candidate"]["status"] == "promoted"
            assert promoted["task"]["name"] == "synthesize_parser_missing_capability_gap"
            assert promoted["task"]["context"]["recommended_publish_target"] == "app_matrix"
            assert promoted["task"]["context"]["capability_gap_blueprint"]["recommended_asset_kind"] == "app"
        finally:
            runtime.close()

    def test_admin_runtime_materializes_skill_draft_from_capability_gap(self, tmp_path):
        host = _DummyHostAgent(root_mode="admin")
        workspace_dir = tmp_path / "workspace"
        skills_dir = workspace_dir / "skills"
        host.skill_registry = SkillRegistry(str(skills_dir))
        host.capability_registry = CapabilityRegistry(
            workspace_dir=workspace_dir,
            capability_bus=CapabilityBus(str(workspace_dir)),
            skill_marketplace=SkillMarketplace(str(workspace_dir)),
            skill_registry=host.skill_registry,
        )

        runtime = PersistentAdminRuntime(
            host_agent=host,
            storage_dir=tmp_path / "runtime",
            poll_interval=0.02,
            max_workers=1,
        )
        try:
            runtime._on_capability_gap_detected(
                Event(
                    type=EventType.CAPABILITY_GAP_DETECTED,
                    source="AdminWatcherDaemon",
                    payload={
                        "source": "worker:toolchain",
                        "event_type": "error",
                        "gap_type": "general_runtime_gap",
                        "suggested_capability_name": "toolchain_general_runtime_gap",
                        "occurrences": 2,
                        "samples": [{"error": "needs reusable helper"}],
                    },
                )
            )

            candidate_id = runtime.list_capability_gap_candidates()[0]["candidate_id"]
            drafted = runtime.draft_capability_gap_candidate(candidate_id)

            assert drafted["success"] is True
            assert drafted["draft"]["asset_kind"] == "skill"
            assert drafted["candidate"]["status"] == "drafted"
            assert drafted["candidate"]["draft_artifact"]["name"] == "toolchain_general_runtime_gap"
            assert host.skill_registry.get_skill("toolchain_general_runtime_gap") is not None
        finally:
            runtime.close()

    def test_admin_runtime_can_close_loop_skill_gap_candidate(self, tmp_path):
        host = _DummyHostAgent(root_mode="admin")
        workspace_dir = tmp_path / "workspace"
        skills_dir = workspace_dir / "skills"
        host.skill_registry = SkillRegistry(str(skills_dir))
        host.skill_marketplace = SkillMarketplace(str(workspace_dir))
        host.capability_registry = CapabilityRegistry(
            workspace_dir=workspace_dir,
            capability_bus=CapabilityBus(str(workspace_dir)),
            skill_marketplace=host.skill_marketplace,
            skill_registry=host.skill_registry,
        )

        runtime = PersistentAdminRuntime(
            host_agent=host,
            storage_dir=tmp_path / "runtime",
            poll_interval=0.02,
            max_workers=1,
        )
        try:
            runtime._on_capability_gap_detected(
                Event(
                    type=EventType.CAPABILITY_GAP_DETECTED,
                    source="AdminWatcherDaemon",
                    payload={
                        "source": "worker:toolchain",
                        "event_type": "error",
                        "gap_type": "general_runtime_gap",
                        "suggested_capability_name": "toolchain_general_runtime_gap",
                        "occurrences": 3,
                        "samples": [{"error": "needs reusable helper"}],
                    },
                )
            )

            candidate_id = runtime.list_capability_gap_candidates()[0]["candidate_id"]
            closed = runtime.close_capability_gap_candidate(candidate_id)

            assert closed["success"] is True
            assert closed["draft"]["asset_kind"] == "skill"
            assert closed["validation"]["valid"] is True
            assert closed["publish"]["success"] is True
            assert closed["candidate"]["status"] == "published"
        finally:
            runtime.close()

    def test_admin_runtime_can_track_rollout_and_resolve_capability_gap(self, tmp_path):
        host = _DummyHostAgent(root_mode="admin")
        workspace_dir = tmp_path / "workspace"
        skills_dir = workspace_dir / "skills"
        host.skill_registry = SkillRegistry(str(skills_dir))
        host.skill_marketplace = SkillMarketplace(str(workspace_dir))
        host.capability_registry = CapabilityRegistry(
            workspace_dir=workspace_dir,
            capability_bus=CapabilityBus(str(workspace_dir)),
            skill_marketplace=host.skill_marketplace,
            skill_registry=host.skill_registry,
        )

        runtime = PersistentAdminRuntime(
            host_agent=host,
            storage_dir=tmp_path / "runtime",
            poll_interval=0.02,
            max_workers=1,
        )
        try:
            runtime._on_capability_gap_detected(
                Event(
                    type=EventType.CAPABILITY_GAP_DETECTED,
                    source="AdminWatcherDaemon",
                    payload={
                        "source": "worker:toolchain",
                        "event_type": "error",
                        "gap_type": "general_runtime_gap",
                        "suggested_capability_name": "toolchain_general_runtime_gap",
                        "occurrences": 2,
                        "samples": [{"error": "needs reusable helper"}],
                    },
                )
            )

            candidate_id = runtime.list_capability_gap_candidates()[0]["candidate_id"]
            closed = runtime.close_capability_gap_candidate(candidate_id)
            rollout = runtime.start_capability_gap_rollout(candidate_id, strategy="shadow", target="ecosystem")
            evaluated = runtime.evaluate_capability_gap_rollout(
                candidate_id,
                outcome="healthy",
                note="post-release telemetry looks clean",
                telemetry_sample={"error_rate": 0.0},
            )

            assert closed["success"] is True
            assert rollout["rollout"]["strategy"] == "shadow"
            assert rollout["candidate"]["status"] == "rollout_active"
            assert evaluated["success"] is True
            assert evaluated["rollout"]["status"] == "verified"
            assert evaluated["candidate"]["status"] == "resolved"
            assert evaluated["candidate"]["post_release_observations"][0]["outcome"] == "healthy"
        finally:
            runtime.close()


# ═══════════════════════════════════════════════════════════════════════════
# 9. Admin Watcher Tests (formerly test_admin_watcher.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestAdminWatcherClass:
    def test_admin_watcher_extracts_capability_gap_candidates(self):
        events = [
            Event(type=EventType.ERROR, source="app:parser", payload={"error": "timeout while parsing"}),
            Event(type=EventType.ERROR, source="app:parser", payload={"error": "timeout while parsing"}),
            Event(type=EventType.SUBAGENT_FAILED, source="agent:builder", payload={"error": "missing tool xyz"}),
            Event(type=EventType.SUBAGENT_FAILED, source="agent:builder", payload={"error": "missing tool xyz"}),
        ]

        candidates = AdminWatcherDaemon.extract_gap_candidates(events)

        assert len(candidates) == 2
        parser_candidate = next(item for item in candidates if item["source"] == "app:parser")
        builder_candidate = next(item for item in candidates if item["source"] == "agent:builder")
        assert parser_candidate["gap_type"] == "latency_or_batching_gap"
        assert builder_candidate["gap_type"] == "missing_capability_gap"
