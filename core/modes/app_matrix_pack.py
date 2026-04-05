"""APP Brain mode pack — multi-app orchestration and topology planning."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.modes.pack import BaseModePack
from core.modes.profile import ModeProfile, resolve_mode_profile


def _build_profile() -> ModeProfile:
    return resolve_mode_profile("app_matrix")


class AppMatrixPack(BaseModePack):
    """Central scheduling agent for cross-app orchestration."""

    def __init__(self) -> None:
        super().__init__(_name="app_matrix", _profile=_build_profile())

    # -- lifecycle ----------------------------------------------------------

    def initialize(self, host: Any) -> None:
        """Wire up orchestration registry, app brain runtime, and admin."""
        _initialize_app_matrix_runtime(host)
        _initialize_persistent_runtime_if_needed(host)

    def teardown(self, host: Any) -> None:
        if host.admin is not None:
            host.admin.stop()

    # -- prompt -------------------------------------------------------------

    def get_prompt_section(self, host: Any) -> str:  # noqa: ARG002
        return "你当前处于 应用矩阵模式，负责应用级协作编排。普通问答保持助手体验；涉及长期演化再升级。"

    # -- API methods --------------------------------------------------------

    def get_api_methods(self) -> dict[str, Callable[..., Any]]:
        return {
            # -- admin surface (inherited) --
            "start_admin_loop": _api_start_admin_loop,
            "stop_admin_loop": _api_stop_admin_loop,
            "submit_admin_goal": _api_submit_admin_goal,
            "plan_admin_goal": _api_plan_admin_goal,
            "list_admin_tasks": _api_list_admin_tasks,
            "get_admin_task": _api_get_admin_task,
            "replan_admin_goal": _api_replan_admin_goal,
            "get_admin_summary": _api_get_admin_summary,
            "list_capability_gap_candidates": _api_list_capability_gap_candidates,
            "get_capability_gap_candidate": _api_get_capability_gap_candidate,
            "draft_capability_gap_candidate": _api_draft_capability_gap_candidate,
            "validate_capability_gap_candidate": _api_validate_capability_gap_candidate,
            "publish_capability_gap_candidate": _api_publish_capability_gap_candidate,
            "close_capability_gap_candidate": _api_close_capability_gap_candidate,
            "start_capability_gap_rollout": _api_start_capability_gap_rollout,
            "evaluate_capability_gap_rollout": _api_evaluate_capability_gap_rollout,
            "promote_capability_gap_candidate": _api_promote_capability_gap_candidate,
            # -- APP Brain specifics --
            "start_app_matrix_loop": _api_start_app_matrix_loop,
            "stop_app_matrix_loop": _api_stop_app_matrix_loop,
            "submit_app_matrix_goal": _api_submit_app_matrix_goal,
            "plan_app_matrix_goal": _api_plan_app_matrix_goal,
            "plan_app_matrix_topology": _api_plan_app_matrix_topology,
            "list_app_matrix_tasks": _api_list_app_matrix_tasks,
            "get_app_matrix_task": _api_get_app_matrix_task,
            "replan_app_matrix_goal": _api_replan_app_matrix_goal,
            "get_app_matrix_summary": _api_get_app_matrix_summary,
            "sync_app_matrix_registry": _api_sync_app_matrix_registry,
            "get_app_matrix_overview": _api_get_app_matrix_overview,
            "get_app_matrix_node_summary": _api_get_app_matrix_node_summary,
            "update_app_matrix_node_metadata": _api_update_app_matrix_node_metadata,
            "connect_app_matrix_apps": _api_connect_app_matrix_apps,
            "register_app_matrix_pipeline": _api_register_app_matrix_pipeline,
            "discover_app_matrix_services": _api_discover_app_matrix_services,
            "request_app_matrix_service_grant": _api_request_app_matrix_service_grant,
            "list_app_matrix_service_grants": _api_list_app_matrix_service_grants,
            "invoke_app_matrix_service": _api_invoke_app_matrix_service,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers (moved from lifecycle.py / app_matrix_ops.py / agent.py)
# ═══════════════════════════════════════════════════════════════════════════


def _initialize_app_matrix_runtime(host: Any) -> None:
    if not host.mode_profile.enables_app_orchestration:
        host.orchestration_registry = None
        host.app_matrix = None
        return

    from core.assets.apps.app_orchestration import AppOrchestrationRegistry
    from core.assets.apps.app_matrix_runtime import AppMatrixRuntime

    orch_path = host.paths.workspace_data_dir / "app_orchestration.json"
    registry = AppOrchestrationRegistry(storage_path=str(orch_path))
    host.runtime.orchestration_registry = registry
    host.orchestration_registry = registry
    host.app_matrix = AppMatrixRuntime(
        app_manager=host.app_manager,
        orchestration_registry=registry,
        capability_registry=host.capability_registry,
    )
    host.app_matrix.sync_apps()


def _ensure_admin_runtime(host: Any) -> Any:
    if host.admin is None:
        host._attach_admin_runtime = True
        _initialize_persistent_runtime_if_needed(host)
    assert host.admin is not None
    return host.admin


def _initialize_persistent_runtime_if_needed(host: Any) -> None:
    if not getattr(host, "_attach_admin_runtime", False) or host.admin is not None:
        return
    from core.modes.admin_runtime import PersistentAdminRuntime

    storage_dir = host._admin_storage_dir or str(host.paths.workspace_data_dir / host.mode_profile.durable_runtime_dir)
    host.admin = PersistentAdminRuntime(
        host_agent=host,
        storage_dir=storage_dir,
        poll_interval=host._admin_poll_interval,
        max_workers=host._admin_workers,
        step_executor=host._admin_step_executor,
    )


def _plan_app_matrix_topology_impl(
    host: Any,
    *,
    goal_name: str,
    goal_description: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from core.assets.apps.app_matrix_planner import AppMatrixPlanner, fallback_app_matrix_plan

    app_inventory: list[Any] = []
    if host.app_matrix is not None:
        app_inventory = _get_app_matrix_overview_impl(host).get("apps", [])

    planner = getattr(host, "_app_matrix_planner", None)
    if planner is None and host.llm is not None:
        planner = AppMatrixPlanner(host.llm)
        host._app_matrix_planner = planner

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


def _sync_app_matrix_registry_impl(host: Any, *, clear_missing: bool = False) -> dict[str, Any]:
    if host.app_matrix is None:
        return {"synced": [], "removed": [], "issues": [], "topology_stats": {}}
    return host.app_matrix.sync_apps(clear_missing=clear_missing)


def _get_app_matrix_overview_impl(host: Any) -> dict[str, Any]:
    if host.app_matrix is None:
        return {"apps": [], "topology": {"nodes": [], "bindings": [], "pipelines": [], "stats": {}}, "issues": []}
    return host.app_matrix.get_overview()


def _get_app_matrix_node_summary_impl(host: Any, app_name: str) -> dict[str, Any] | None:
    if host.app_matrix is None:
        return None
    return host.app_matrix.get_app_summary(app_name)


# ═══════════════════════════════════════════════════════════════════════════
# API callables  (host, *args, **kwargs)
# ═══════════════════════════════════════════════════════════════════════════

# -- admin surface (same as admin_pack) ---


def _api_start_admin_loop(host: Any) -> None:
    host.require_mode_capability("durable_goal_loop", surface="start_admin_loop")
    _ensure_admin_runtime(host).start()


def _api_stop_admin_loop(host: Any) -> None:
    if host.admin is not None:
        host.admin.stop()


def _api_submit_admin_goal(
    host: Any,
    *,
    name: str,
    description: str,
    steps: list[str] | None = None,
    context: dict[str, Any] | None = None,
    auto_start: bool = True,
    auto_plan: bool = True,
    max_steps: int = 50,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="submit_admin_goal")
    task = _ensure_admin_runtime(host).submit_goal(
        name=name,
        description=description,
        steps=steps,
        context=context,
        auto_start=auto_start,
        auto_plan=auto_plan,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
    )
    return task.to_dict()


def _api_plan_admin_goal(
    host: Any,
    *,
    name: str,
    description: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="plan_admin_goal")
    plan = _ensure_admin_runtime(host).plan_goal(
        name=name,
        description=description,
        context=context,
    )
    return plan.model_dump()


def _api_list_admin_tasks(host: Any) -> list[dict[str, Any]]:
    host.require_mode_capability("durable_goal_loop", surface="list_admin_tasks")
    if host.admin is None:
        return []
    return [t.to_dict() for t in host.admin.list_tasks()]


def _api_get_admin_task(host: Any, task_id: str) -> dict[str, Any] | None:
    host.require_mode_capability("durable_goal_loop", surface="get_admin_task")
    if host.admin is None:
        return None
    task = host.admin.get_task(task_id)
    return task.to_dict() if task is not None else None


def _api_replan_admin_goal(
    host: Any,
    task_id: str,
    *,
    reason: str = "",
    replacement_steps: list[str] | None = None,
) -> dict[str, Any] | None:
    host.require_mode_capability("durable_goal_loop", surface="replan_admin_goal")
    if host.admin is None:
        return None
    plan = host.admin.replan_task(task_id, reason=reason, replacement_steps=replacement_steps)
    return plan.model_dump() if plan is not None else None


def _api_get_admin_summary(host: Any) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="get_admin_summary")
    if host.admin is None:
        return {"tasks": {}, "active_queue_jobs": 0, "inflight_task_ids": []}
    return host.admin.get_summary()


def _api_list_capability_gap_candidates(
    host: Any,
    *,
    status: str = "",
) -> list[dict[str, Any]]:
    host.require_mode_capability("durable_goal_loop", surface="list_capability_gap_candidates")
    return _ensure_admin_runtime(host).list_capability_gap_candidates(status=status)


def _api_get_capability_gap_candidate(
    host: Any,
    candidate_id: str,
) -> dict[str, Any] | None:
    host.require_mode_capability("durable_goal_loop", surface="get_capability_gap_candidate")
    return _ensure_admin_runtime(host).get_capability_gap_candidate(candidate_id)


def _api_draft_capability_gap_candidate(
    host: Any,
    candidate_id: str,
    *,
    target_name: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="draft_capability_gap_candidate")
    return _ensure_admin_runtime(host).draft_capability_gap_candidate(
        candidate_id,
        target_name=target_name,
        overwrite=overwrite,
    )


def _api_validate_capability_gap_candidate(
    host: Any,
    candidate_id: str,
) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="validate_capability_gap_candidate")
    return _ensure_admin_runtime(host).validate_capability_gap_candidate(candidate_id)


def _api_publish_capability_gap_candidate(
    host: Any,
    candidate_id: str,
    *,
    publish_to_hub: bool = False,
    hub_url: str = "",
    hub_token: str = "",
    version: str = "0.1.0",
    changelog: str = "",
) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="publish_capability_gap_candidate")
    return _ensure_admin_runtime(host).publish_capability_gap_candidate(
        candidate_id,
        publish_to_hub=publish_to_hub,
        hub_url=hub_url,
        hub_token=hub_token,
        version=version,
        changelog=changelog,
    )


def _api_close_capability_gap_candidate(
    host: Any,
    candidate_id: str,
    *,
    target_name: str = "",
    overwrite: bool = False,
    publish_to_hub: bool = False,
    hub_url: str = "",
    hub_token: str = "",
    version: str = "0.1.0",
    changelog: str = "",
) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="close_capability_gap_candidate")
    return _ensure_admin_runtime(host).close_capability_gap_candidate(
        candidate_id,
        target_name=target_name,
        overwrite=overwrite,
        publish_to_hub=publish_to_hub,
        hub_url=hub_url,
        hub_token=hub_token,
        version=version,
        changelog=changelog,
    )


def _api_start_capability_gap_rollout(
    host: Any,
    candidate_id: str,
    *,
    strategy: str = "shadow",
    target: str = "",
    note: str = "",
) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="start_capability_gap_rollout")
    return _ensure_admin_runtime(host).start_capability_gap_rollout(
        candidate_id,
        strategy=strategy,
        target=target,
        note=note,
    )


def _api_evaluate_capability_gap_rollout(
    host: Any,
    candidate_id: str,
    *,
    outcome: str,
    note: str = "",
    rollout_id: str = "",
    telemetry_sample: dict[str, Any] | None = None,
    close_on_healthy: bool = True,
) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="evaluate_capability_gap_rollout")
    return _ensure_admin_runtime(host).evaluate_capability_gap_rollout(
        candidate_id,
        outcome=outcome,
        note=note,
        rollout_id=rollout_id,
        telemetry_sample=telemetry_sample,
        close_on_healthy=close_on_healthy,
    )


def _api_promote_capability_gap_candidate(
    host: Any,
    candidate_id: str,
    *,
    auto_start: bool = True,
) -> dict[str, Any]:
    host.require_mode_capability("durable_goal_loop", surface="promote_capability_gap_candidate")
    return _ensure_admin_runtime(host).promote_capability_gap_candidate(
        candidate_id,
        auto_start=auto_start,
    )


# -- app brain specifics ---


def _api_start_app_matrix_loop(host: Any) -> None:
    host.require_mode_capability("app_orchestration", surface="start_app_matrix_loop")
    _api_start_admin_loop(host)


def _api_stop_app_matrix_loop(host: Any) -> None:
    _api_stop_admin_loop(host)


def _api_submit_app_matrix_goal(
    host: Any,
    *,
    name: str,
    description: str,
    steps: list[str] | None = None,
    context: dict[str, Any] | None = None,
    auto_start: bool = True,
    auto_plan: bool = True,
    max_steps: int = 50,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="submit_app_matrix_goal")
    return _api_submit_admin_goal(
        host,
        name=name,
        description=description,
        steps=steps,
        context=context,
        auto_start=auto_start,
        auto_plan=auto_plan,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
    )


def _api_plan_app_matrix_goal(
    host: Any,
    *,
    name: str,
    description: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="plan_app_matrix_goal")
    return _api_plan_admin_goal(host, name=name, description=description, context=context)


def _api_plan_app_matrix_topology(
    host: Any,
    *,
    goal_name: str,
    goal_description: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host.require_mode_capability("app_topology_planning", surface="plan_app_matrix_topology")
    return _plan_app_matrix_topology_impl(host, goal_name=goal_name, goal_description=goal_description, context=context)


def _api_list_app_matrix_tasks(host: Any) -> list[dict[str, Any]]:
    host.require_mode_capability("app_orchestration", surface="list_app_matrix_tasks")
    return _api_list_admin_tasks(host)


def _api_get_app_matrix_task(host: Any, task_id: str) -> dict[str, Any] | None:
    host.require_mode_capability("app_orchestration", surface="get_app_matrix_task")
    return _api_get_admin_task(host, task_id)


def _api_replan_app_matrix_goal(
    host: Any,
    task_id: str,
    *,
    reason: str = "",
    replacement_steps: list[str] | None = None,
) -> dict[str, Any] | None:
    host.require_mode_capability("app_orchestration", surface="replan_app_matrix_goal")
    return _api_replan_admin_goal(host, task_id, reason=reason, replacement_steps=replacement_steps)


def _api_get_app_matrix_summary(host: Any) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="get_app_matrix_summary")
    return _api_get_admin_summary(host)


def _api_sync_app_matrix_registry(host: Any, *, clear_missing: bool = False) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="sync_app_matrix_registry")
    return _sync_app_matrix_registry_impl(host, clear_missing=clear_missing)


def _api_get_app_matrix_overview(host: Any) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="get_app_matrix_overview")
    return _get_app_matrix_overview_impl(host)


def _api_get_app_matrix_node_summary(host: Any, app_name: str) -> dict[str, Any] | None:
    host.require_mode_capability("app_orchestration", surface="get_app_matrix_node_summary")
    return _get_app_matrix_node_summary_impl(host, app_name)


def _api_update_app_matrix_node_metadata(
    host: Any,
    app_name: str,
    *,
    shared_datastores: list[str] | None = None,
    shared_schemas: list[dict[str, Any]] | None = None,
    data_contracts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="update_app_matrix_node_metadata")
    if host.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host.app_matrix.update_app_contract_metadata(
        app_name,
        shared_datastores=shared_datastores,
        shared_schemas=shared_schemas,
        data_contracts=data_contracts,
    )


def _api_connect_app_matrix_apps(
    host: Any,
    source_app: str,
    target_app: str,
    *,
    source_port: str = "default",
    target_port: str = "default",
    description: str = "",
    transform: str = "",
) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="connect_app_matrix_apps")
    if host.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host.app_matrix.connect_apps(
        source_app,
        target_app,
        source_port=source_port,
        target_port=target_port,
        description=description,
        transform=transform,
    )


def _api_register_app_matrix_pipeline(
    host: Any,
    *,
    name: str,
    app_names: list[str],
    description: str = "",
    schedule: str = "",
) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="register_app_matrix_pipeline")
    if host.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host.app_matrix.register_pipeline(
        name,
        app_names,
        description=description,
        schedule=schedule,
    )


def _api_discover_app_matrix_services(
    host: Any,
    *,
    query: str = "",
    provides: str = "",
) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="discover_app_matrix_services")
    if host.app_matrix is None:
        return {"query": query, "provides": provides, "providers": [], "count": 0}
    return host.app_matrix.discover_services(query=query, provides=provides)


def _api_request_app_matrix_service_grant(
    host: Any,
    *,
    caller_app: str,
    target_app: str = "",
    capability_name: str = "",
    provides: str = "",
    requested_quota: int = 1,
    ttl_seconds: int = 3600,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="request_app_matrix_service_grant")
    if host.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host.app_matrix.request_service_grant(
        caller_app=caller_app,
        target_app=target_app,
        capability_name=capability_name,
        provides=provides,
        requested_quota=requested_quota,
        ttl_seconds=ttl_seconds,
        metadata=metadata,
    )


def _api_list_app_matrix_service_grants(
    host: Any,
    *,
    caller_app: str = "",
    provider_app: str = "",
) -> list[dict[str, Any]]:
    host.require_mode_capability("app_orchestration", surface="list_app_matrix_service_grants")
    if host.app_matrix is None:
        return []
    return host.app_matrix.list_service_grants(
        caller_app=caller_app,
        provider_app=provider_app,
    )


def _api_invoke_app_matrix_service(
    host: Any,
    *,
    caller_app: str,
    grant_token: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    host.require_mode_capability("app_orchestration", surface="invoke_app_matrix_service")
    if host.app_matrix is None:
        raise RuntimeError("APP Brain mode is not active")
    return host.app_matrix.invoke_service(
        caller_app=caller_app,
        grant_token=grant_token,
        action=action,
        payload=payload,
    )
