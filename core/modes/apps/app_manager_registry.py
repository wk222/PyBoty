"""Shared AppManager registry for app-related tools and services."""

from __future__ import annotations

from core.systems.runtime.project_paths import ProjectPaths

from .app_manager import AppManager

_app_manager: AppManager | None = None


def get_shared_app_manager() -> AppManager:
    """Return the shared AppManager instance used by app tooling."""
    global _app_manager
    if _app_manager is None:
        paths = ProjectPaths.from_root()
        _app_manager = AppManager(str(paths.apps_dir), project_paths=paths)
    return _app_manager


def set_shared_app_manager(manager: AppManager) -> None:
    """Override the shared AppManager instance."""
    global _app_manager
    _app_manager = manager
