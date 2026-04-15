"""APP Brain mode helpers."""

from __future__ import annotations

from typing import Any

from core.modes.apps.app_matrix_planner import AppMatrixPlanner, fallback_app_matrix_plan


def plan_app_matrix_topology(
    host_agent: Any,
    *,
    goal_name: str,
    goal_description: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    app_inventory = []
    if host_agent.app_matrix is not None:
        app_inventory = get_app_matrix_overview(host_agent).get("apps", [])

    planner = getattr(host_agent, "_app_matrix_planner", None)
    if planner is None and host_agent.llm is not None:
        planner = AppMatrixPlanner(host_agent.llm)
        host_agent._app_matrix_planner = planner

    if planner is None:
        plan = fallback_app_matrix_plan(
            goal_name=goal_name,
            goal_description=goal_description,
            app_inventory=app_inventory,
            error="planner_llm_unavailable",
        )
        return plan.model_dump()

    try:
        plan = planner.plan_topology(
            goal_name=goal_name,
            goal_description=goal_description,
            app_inventory=app_inventory,
            context=context,
        )
    except Exception as exc:
        plan = fallback_app_matrix_plan(
            goal_name=goal_name,
            goal_description=goal_description,
            app_inventory=app_inventory,
            error=str(exc),
        )
    return plan.model_dump()


def sync_app_matrix_registry(host_agent: Any, *, clear_missing: bool = False) -> dict[str, Any]:
    if host_agent.app_matrix is None:
        return {"synced": [], "removed": [], "issues": [], "topology_stats": {}}
    return host_agent.app_matrix.sync_apps(clear_missing=clear_missing)


def get_app_matrix_overview(host_agent: Any) -> dict[str, Any]:
    if host_agent.app_matrix is None:
        return {"apps": [], "topology": {"nodes": [], "bindings": [], "pipelines": [], "stats": {}}, "issues": []}
    return host_agent.app_matrix.get_overview()


def get_app_matrix_node_summary(host_agent: Any, app_name: str) -> dict[str, Any] | None:
    if host_agent.app_matrix is None:
        return None
    return host_agent.app_matrix.get_app_summary(app_name)


def update_app_matrix_node_metadata(
    host_agent: Any,
    app_name: str,
    *,
    shared_datastores: list[str] | None = None,
    shared_schemas: list[dict[str, Any]] | None = None,
    data_contracts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if host_agent.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host_agent.app_matrix.update_app_contract_metadata(
        app_name,
        shared_datastores=shared_datastores,
        shared_schemas=shared_schemas,
        data_contracts=data_contracts,
    )


def connect_app_matrix_apps(
    host_agent: Any,
    source_app: str,
    target_app: str,
    *,
    source_port: str = "default",
    target_port: str = "default",
    description: str = "",
    transform: str = "",
) -> dict[str, Any]:
    if host_agent.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host_agent.app_matrix.connect_apps(
        source_app,
        target_app,
        source_port=source_port,
        target_port=target_port,
        description=description,
        transform=transform,
    )


def register_app_matrix_pipeline(
    host_agent: Any,
    *,
    name: str,
    app_names: list[str],
    description: str = "",
    schedule: str = "",
) -> dict[str, Any]:
    if host_agent.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host_agent.app_matrix.register_pipeline(
        name,
        app_names,
        description=description,
        schedule=schedule,
    )


def discover_app_matrix_services(
    host_agent: Any,
    *,
    query: str = "",
    provides: str = "",
) -> dict[str, Any]:
    if host_agent.app_matrix is None:
        return {"query": query, "provides": provides, "providers": [], "count": 0}
    return host_agent.app_matrix.discover_services(query=query, provides=provides)


def request_app_matrix_service_grant(
    host_agent: Any,
    *,
    caller_app: str,
    target_app: str = "",
    capability_name: str = "",
    provides: str = "",
    requested_quota: int = 1,
    ttl_seconds: int = 3600,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if host_agent.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host_agent.app_matrix.request_service_grant(
        caller_app=caller_app,
        target_app=target_app,
        capability_name=capability_name,
        provides=provides,
        requested_quota=requested_quota,
        ttl_seconds=ttl_seconds,
        metadata=metadata,
    )


def list_app_matrix_service_grants(
    host_agent: Any,
    *,
    caller_app: str = "",
    provider_app: str = "",
) -> list[dict[str, Any]]:
    if host_agent.app_matrix is None:
        return []
    return host_agent.app_matrix.list_service_grants(
        caller_app=caller_app,
        provider_app=provider_app,
    )


def invoke_app_matrix_service(
    host_agent: Any,
    *,
    caller_app: str,
    grant_token: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if host_agent.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host_agent.app_matrix.invoke_service(
        caller_app=caller_app,
        grant_token=grant_token,
        action=action,
        payload=payload,
    )
