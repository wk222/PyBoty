"""Helpers for exposing ModePack APIs on the host PyBot class."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.modes.builtin_packs import ensure_builtin_packs
from core.modes.pack import get_global_registry

MODE_API_CAPABILITIES: dict[str, str] = {
    "start_admin_loop": "durable_goal_loop",
    "stop_admin_loop": "durable_goal_loop",
    "submit_admin_goal": "durable_goal_loop",
    "plan_admin_goal": "durable_goal_loop",
    "list_admin_tasks": "durable_goal_loop",
    "get_admin_task": "durable_goal_loop",
    "replan_admin_goal": "durable_goal_loop",
    "get_admin_summary": "durable_goal_loop",
    "list_capability_gap_candidates": "durable_goal_loop",
    "get_capability_gap_candidate": "durable_goal_loop",
    "draft_capability_gap_candidate": "durable_goal_loop",
    "validate_capability_gap_candidate": "durable_goal_loop",
    "publish_capability_gap_candidate": "durable_goal_loop",
    "close_capability_gap_candidate": "durable_goal_loop",
    "start_capability_gap_rollout": "durable_goal_loop",
    "evaluate_capability_gap_rollout": "durable_goal_loop",
    "promote_capability_gap_candidate": "durable_goal_loop",
    "start_app_matrix_loop": "app_orchestration",
    "stop_app_matrix_loop": "app_orchestration",
    "submit_app_matrix_goal": "app_orchestration",
    "plan_app_matrix_goal": "app_orchestration",
    "plan_app_matrix_topology": "app_topology_planning",
    "list_app_matrix_tasks": "app_orchestration",
    "get_app_matrix_task": "app_orchestration",
    "replan_app_matrix_goal": "app_orchestration",
    "get_app_matrix_summary": "app_orchestration",
    "sync_app_matrix_registry": "app_orchestration",
    "get_app_matrix_overview": "app_orchestration",
    "get_app_matrix_node_summary": "app_orchestration",
    "update_app_matrix_node_metadata": "app_orchestration",
    "connect_app_matrix_apps": "app_orchestration",
    "register_app_matrix_pipeline": "app_orchestration",
    "discover_app_matrix_services": "app_orchestration",
    "request_app_matrix_service_grant": "app_orchestration",
    "list_app_matrix_service_grants": "app_orchestration",
    "invoke_app_matrix_service": "app_orchestration",
}


def get_mode_api_capability(method_name: str) -> str | None:
    """Return the capability required for a public mode API, if any."""
    return MODE_API_CAPABILITIES.get(method_name)


def list_registered_mode_api_names() -> list[str]:
    """Return the union of API methods declared by all registered mode packs."""
    ensure_builtin_packs()
    names = set(MODE_API_CAPABILITIES)
    for pack in get_global_registry().list_all():
        names.update(pack.get_api_methods().keys())
    return sorted(names)


def attach_mode_surface_methods(host_cls: type[Any]) -> None:
    """Attach thin dispatch wrappers for all registered mode APIs."""
    for method_name in list_registered_mode_api_names():
        if method_name in host_cls.__dict__:
            continue
        setattr(host_cls, method_name, _build_mode_surface_method(method_name))


def resolve_mode_surface_method(instance: Any, method_name: str) -> Callable[..., Any] | None:
    """Resolve a late-bound mode API wrapper for new packs added after import."""
    if method_name not in list_registered_mode_api_names():
        return None

    def _bound(*args: Any, **kwargs: Any) -> Any:
        return instance._dispatch_mode_method(method_name, *args, **kwargs)

    _bound.__name__ = method_name
    _bound.__doc__ = f"Late-bound wrapper for mode API `{method_name}`."
    return _bound


def _build_mode_surface_method(method_name: str) -> Callable[..., Any]:
    def _method(self: Any, *args: Any, **kwargs: Any) -> Any:
        return self._dispatch_mode_method(method_name, *args, **kwargs)

    _method.__name__ = method_name
    _method.__qualname__ = method_name
    _method.__doc__ = f"Mode-pack surface wrapper for `{method_name}`."
    return _method
