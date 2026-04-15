from __future__ import annotations

from core.modes.apps.app_manager import AppManager
from core.modes.apps.app_orchestration import AppOrchestrationRegistry, NodeType
from core.modes.apps.app_matrix_runtime import AppMatrixRuntime


def test_app_matrix_runtime_syncs_apps_into_registry(temp_paths):
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


def test_app_matrix_runtime_builds_bindings_and_pipeline(temp_paths):
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


def test_app_matrix_runtime_updates_contract_metadata_and_summary(temp_paths):
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


def test_app_matrix_runtime_discovers_services_and_invokes_with_grant(temp_paths):
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

    from core.assets.skills.skill_marketplace import SkillMarketplace
    from core.systems.bus.capability_bus import CapabilityBus
    from core.systems.bus.capability_registry import CapabilityRegistry

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


def test_app_matrix_runtime_enforces_provider_policy_limits(temp_paths):
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

    from core.assets.skills.skill_marketplace import SkillMarketplace
    from core.systems.bus.capability_bus import CapabilityBus
    from core.systems.bus.capability_registry import CapabilityRegistry

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
