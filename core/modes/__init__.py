"""Target package for root-mode runtimes.

This package exists as the migration home for:
- assistant mode runtime
- APP brain mode runtime
- admin mode runtime
- root-mode factories and shared mode wiring
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "attach_mode_surface_methods": (".api_surface", "attach_mode_surface_methods"),
    "build_mode_subclasses": (".factories", "build_mode_subclasses"),
    "connect_app_matrix_apps": (".app_matrix_ops", "connect_app_matrix_apps"),
    "create_mode_agent": (".factories", "create_mode_agent"),
    "ensure_admin_runtime": (".lifecycle", "ensure_admin_runtime"),
    "get_mode_capability_label": (".profile", "get_mode_capability_label"),
    "get_app_matrix_node_summary": (".app_matrix_ops", "get_app_matrix_node_summary"),
    "get_mode_api_capability": (".api_surface", "get_mode_api_capability"),
    "get_app_matrix_overview": (".app_matrix_ops", "get_app_matrix_overview"),
    "initialize_mode_services": (".lifecycle", "initialize_mode_services"),
    "list_mode_profiles": (".profile", "list_mode_profiles"),
    "plan_app_matrix_topology": (".app_matrix_ops", "plan_app_matrix_topology"),
    "print_startup_summary": (".lifecycle", "print_startup_summary"),
    "register_app_matrix_pipeline": (".app_matrix_ops", "register_app_matrix_pipeline"),
    "resolve_mode_profile": (".profile", "resolve_mode_profile"),
    "resolve_mode_surface_method": (".api_surface", "resolve_mode_surface_method"),
    "should_attach_admin_runtime": (".lifecycle", "should_attach_admin_runtime"),
    "sync_app_matrix_registry": (".app_matrix_ops", "sync_app_matrix_registry"),
    "update_app_matrix_node_metadata": (".app_matrix_ops", "update_app_matrix_node_metadata"),
    # -- ExecutionCanvas --
    "CanvasProfile": (".canvas", "CanvasProfile"),
    "get_canvas_profile": (".canvas", "get_canvas_profile"),
    "list_canvas_profiles": (".canvas", "list_canvas_profiles"),
    "DEFAULT_CANVAS": (".canvas", "DEFAULT_CANVAS"),
    "CANVAS_NAMES": (".canvas", "CANVAS_NAMES"),
    # -- 身份层统一 API（推荐 web/ 层使用的入口）--
    "get_session_config": (".api", "get_session_config"),
    # -- ModePack registry API --
    "ModePack": (".pack", "ModePack"),
    "BaseModePack": (".pack", "BaseModePack"),
    "ModePackRegistry": (".pack", "ModePackRegistry"),
    "get_global_registry": (".pack", "get_global_registry"),
    "register_mode_pack": (".pack", "register_mode_pack"),
    "get_mode_pack": (".pack", "get_mode_pack"),
    "ensure_builtin_packs": (".builtin_packs", "ensure_builtin_packs"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = _EXPORTS[name]
    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
