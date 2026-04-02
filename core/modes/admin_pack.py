"""Admin mode pack — durable goal loop and long-running autonomy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.modes.pack import BaseModePack
from core.modes.profile import ModeProfile, resolve_mode_profile


def _build_profile() -> ModeProfile:
    return resolve_mode_profile("admin")


class AdminPack(BaseModePack):
    """Long-running admin orchestration with durable goal loop."""

    def __init__(self) -> None:
        super().__init__(_name="admin", _profile=_build_profile())

    # -- lifecycle ----------------------------------------------------------

    def initialize(self, host: Any) -> None:
        """Attach the persistent admin runtime if requested."""
        _initialize_persistent_runtime_if_needed(host)

    def teardown(self, host: Any) -> None:
        if host.admin is not None:
            host.admin.stop()

    # -- prompt -------------------------------------------------------------

    def get_prompt_section(self, host: Any) -> str:  # noqa: ARG002
        return "你当前处于全局管理员模式，负责长期目标与能力创造。通过治理、审批和恢复机制保持可控。"

    # -- API methods --------------------------------------------------------

    def get_api_methods(self) -> dict[str, Callable[..., Any]]:
        return {
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
        }


# ---------------------------------------------------------------------------
# Internal helpers (moved from lifecycle.py / agent.py)
# ---------------------------------------------------------------------------


def _ensure_admin_runtime(host: Any) -> Any:
    """Make sure the host has a persistent runtime attached."""

    if host.admin is None:
        host._attach_admin_runtime = True
        _initialize_persistent_runtime_if_needed(host)
    assert host.admin is not None
    return host.admin


def _initialize_persistent_runtime_if_needed(host: Any) -> None:
    if not getattr(host, "_attach_admin_runtime", False) or host.admin is not None:
        return
    from core.admin_runtime import PersistentAdminRuntime
    from core.systems.runtime.event_bus import Event, EventType, event_bus

    storage_dir = host._admin_storage_dir or str(host.paths.workspace_data_dir / host.mode_profile.durable_runtime_dir)
    host.admin = PersistentAdminRuntime(
        host_agent=host,
        storage_dir=storage_dir,
        poll_interval=host._admin_poll_interval,
        max_workers=host._admin_workers,
        step_executor=host._admin_step_executor,
    )

    # Subscribe to telemetry reports to self-evolve
    def _on_telemetry_report(event: Event) -> None:
        if not host.admin:
            return
        payload = event.payload
        report_content = payload.get("report_content", "")
        if not report_content:
            return

        import time

        host.admin.submit_goal(
            name=f"Auto-Repair from Telemetry {int(time.time())}",
            description="Analyze the latest telemetry report and execute necessary repairs or capability additions.",
            context={"telemetry_report": report_content},
            auto_plan=True,
            auto_start=True,
        )

    event_bus.subscribe(EventType.TELEMETRY_REPORT_GENERATED, _on_telemetry_report)


# ---------------------------------------------------------------------------
# API callables  (host, *args, **kwargs)
# ---------------------------------------------------------------------------


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
    return [task.to_dict() for task in host.admin.list_tasks()]


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
    plan = host.admin.replan_task(
        task_id,
        reason=reason,
        replacement_steps=replacement_steps,
    )
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
