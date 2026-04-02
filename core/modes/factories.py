"""Factory helpers for PyBot root modes."""

from __future__ import annotations

from typing import Any


def build_mode_subclasses(pybot_cls: type[Any]) -> tuple[type[Any], type[Any], type[Any]]:
    """Create the public root-mode subclasses from the base PyBot runtime."""

    class AdminPyBot(pybot_cls):
        """Separate root runtime for long-running admin orchestration."""

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("root_mode", "admin")
            kwargs.setdefault("attach_admin_runtime", True)
            super().__init__(*args, **kwargs)

    class UltimatePyBot(AdminPyBot):
        """User-facing alias for the ultimate-agent mode."""

    class AppMatrixPyBot(pybot_cls):
        """Root runtime for APP-level orchestration and central scheduling."""

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("root_mode", "app_matrix")
            kwargs.setdefault("attach_admin_runtime", True)
            super().__init__(*args, **kwargs)

    return AdminPyBot, UltimatePyBot, AppMatrixPyBot


def create_mode_agent(
    agent_cls: type[Any],
    *,
    model: str,
    thread_id: str,
    paths: Any = None,
    control_config: dict[str, Any] | None = None,
    approval_queue: Any = None,
    **kwargs,
) -> Any:
    """Instantiate a concrete root-mode runtime with the standard public kwargs."""
    return agent_cls(
        model=model,
        thread_id=thread_id,
        paths=paths,
        control_config=control_config,
        approval_queue=approval_queue,
        **kwargs,
    )
