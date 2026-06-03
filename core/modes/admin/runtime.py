"""Persistent admin runtime for long-running root-agent tasks."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.systems.memory.admin_memory import AdminMemoryManager, create_llm_summarizer
from core.systems.agents.persistent_agent_runner import PersistentAgentRunner, PersistentTask, PersistentTaskStatus, PersistentTaskStep
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.runtime.event_bus import Event, EventType, event_bus
from core.assets.workflows.task_queue import TaskQueue

from core.modes.admin.capability_gap_candidate import CapabilityGapCandidate
from core.modes.admin.planner import AdminPlan, AdminPlanner, fallback_admin_plan
from core.modes.admin.prompts import build_admin_step_prompt
from core.modes.capability_synthesis import (
    materialize_capability_gap_draft,
    publish_capability_gap_draft,
    validate_capability_gap_draft,
)
from core.modes.system_model import normalize_root_mode

__all__ = [
    "CapabilityGapCandidate",
    "PersistentAdminRuntime",
    "build_admin_step_prompt",
]


class PersistentAdminRuntime:
    """Background loop that keeps durable admin tasks moving forward."""

    def __init__(
        self,
        *,
        host_agent: Any,
        storage_dir: str | Path,
        poll_interval: float = 2.0,
        max_workers: int = 2,
        step_executor: Callable[[PersistentTask, PersistentTaskStep, dict[str, Any]], Any] | None = None,
        goal_planner: Callable[[str, str, dict[str, Any]], AdminPlan | dict[str, Any] | list[str]] | None = None,
        summarize_fn: Callable[[str], str] | None = None,
        approval_queue: ApprovalQueue | None = None,
        subagent_registry: Any | None = None,
    ) -> None:
        self.host_agent = host_agent
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.runner = PersistentAgentRunner(str(self.storage_dir))
        self.queue = TaskQueue(max_workers=max_workers, max_history=500)
        self.poll_interval = poll_interval
        self._step_executor = step_executor or self._default_step_executor
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        self._last_queue_task_ids: dict[str, str] = {}
        self._goal_planner = goal_planner
        self._planner: AdminPlanner | None = None
        self._approval_queue = approval_queue or getattr(host_agent, "approval_queue", None)
        self._subagent_registry = subagent_registry or getattr(host_agent, "subagent_registry", None)
        resolved_summarize_fn = summarize_fn or create_llm_summarizer(getattr(host_agent, "llm", None))
        self._memory = AdminMemoryManager(summarize_fn=resolved_summarize_fn)
        self._capability_gap_path = self.storage_dir / "capability_gap_candidates.json"
        self._capability_gap_candidates: dict[str, CapabilityGapCandidate] = self._load_capability_gap_candidates()
        event_bus.subscribe(EventType.CAPABILITY_GAP_DETECTED, self._on_capability_gap_detected)
        event_bus.subscribe(EventType.APP_RUNTIME_ERROR, self._on_app_runtime_error)

    def _on_app_runtime_error(self, event: Event) -> None:
        payload = event.payload
        app_name = payload.get("app_name")
        action = payload.get("action")
        error_msg = payload.get("error")
        stderr = payload.get("stderr", "")

        if not app_name or not error_msg:
            return

        # Avoid creating duplicate repair tasks for the same app
        for task in self.runner.list_tasks():
            if task.status in (PersistentTaskStatus.PENDING, PersistentTaskStatus.RUNNING) and task.name == f"repair_app_{app_name}":
                return

        description = (
            f"The application '{app_name}' encountered a runtime error during API action '{action}'.\n"
            f"Error details: {error_msg}\n"
            f"Stderr: {stderr}\n\n"
            "Please use `build_app_iteratively` to automatically verify and repair the application."
        )
        self.submit_goal(
            name=f"repair_app_{app_name}",
            description=description,
            steps=[
                f"Analyze the error logs for app {app_name}",
                f"Use build_app_iteratively to test and fix the code",
                "Verify the app functions correctly again",
            ],
            auto_start=True,
            auto_plan=False,
        )

    def spawn_subagent(
        self,
        *,
        agent_name: str,
        task: str,
        context: str = "",
        timeout_seconds: float | None = None,
    ) -> str:
        """Helper to spawn a subagent from the admin runtime using the scheduler."""
        if not hasattr(self.host_agent.runtime, "swarm_scheduler"):
            raise RuntimeError("Swarm scheduler not available in runtime")
            
        scheduler = self.host_agent.runtime.swarm_scheduler
        from core.systems.runtime.pybot_bootstrap import invoke_sub_agent
        
        return scheduler.spawn_managed(
            agent_name=agent_name,
            task=task,
            invoke_fn=invoke_sub_agent,
            invoke_kwargs={
                "agent_storage": self.host_agent.agent_storage,
                "global_tool_storage": self.host_agent.tool_storage,
                "llm_factory": self.host_agent._create_llm,
                "control_policy": self.host_agent.control_policy,
                "approval_queue": self.host_agent.approval_queue,
                "project_paths": self.host_agent.project_paths,
                "context": context,
                "thread_id": self.host_agent.thread_id,
            },
            parent_agent_name="admin_runtime",
            timeout_seconds=timeout_seconds,
        )

    def wait_subagent(self, run_id: str, timeout: float = 60.0) -> dict[str, Any]:
        """Wait for a subagent and return results using the scheduler."""
        if not hasattr(self.host_agent.runtime, "swarm_scheduler"):
            raise RuntimeError("Swarm scheduler not available in runtime")
            
        scheduler = self.host_agent.runtime.swarm_scheduler
        return scheduler.wait_for_task(run_id, timeout=timeout)

    def submit_goal(
        self,
        *,
        name: str,
        description: str,
        steps: list[str] | None = None,
        context: dict[str, Any] | None = None,
        agent_name: str | None = None,
        auto_start: bool = True,
        auto_plan: bool = True,
        max_steps: int = 50,
        timeout_seconds: float | None = None,
    ) -> PersistentTask:
        """Create a durable admin task and optionally queue it immediately."""
        resolved_steps = [step for step in (steps or []) if str(step).strip()]
        planning_context = dict(context or {})
        if auto_plan and not resolved_steps:
            plan = self.plan_goal(name=name, description=description, context=planning_context)
            resolved_steps = list(plan.steps)
            planning_context["admin_plan"] = plan.model_dump()
            planning_context["success_criteria"] = list(plan.success_criteria)
            planning_context["plan_summary"] = plan.summary
            if plan.planning_notes:
                planning_context["planning_notes"] = plan.planning_notes
        elif not resolved_steps:
            resolved_steps = [description or name]

        task = self.runner.create_task(
            name=name,
            description=description,
            agent_name=agent_name or self._default_agent_name(),
            steps=resolved_steps,
            context=planning_context,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
        )
        if auto_start:
            self.tick_once()
        return task

    def plan_goal(
        self,
        *,
        name: str,
        description: str,
        context: dict[str, Any] | None = None,
    ) -> AdminPlan:
        """Build a structured plan for a durable admin goal."""
        resolved_context = dict(context or {})
        
        if self.host_agent is not None:
            from core.systems.context.projected_runtime_view import ProjectedRuntimeView
            try:
                view = ProjectedRuntimeView.compile(runtime=self.host_agent)
                resolved_context["projected_runtime_view"] = view.to_payload()
            except Exception:
                pass

        if self._goal_planner is not None:
            try:
                return self._normalize_plan_output(
                    self._goal_planner(name, description, resolved_context),
                    name=name,
                    description=description,
                )
            except Exception as exc:
                return fallback_admin_plan(name=name, description=description, error=str(exc))

        planner = self._get_default_planner()
        if planner is None:
            return fallback_admin_plan(
                name=name,
                description=description,
                error="planner_llm_unavailable",
            )
        try:
            return planner.plan_goal(
                name=name,
                description=description,
                context=resolved_context,
            )
        except Exception as exc:
            return fallback_admin_plan(name=name, description=description, error=str(exc))

    def list_tasks(self, status: PersistentTaskStatus | None = None) -> list[PersistentTask]:
        return self.runner.list_tasks(status=status)

    def get_task(self, task_id: str) -> PersistentTask | None:
        return self.runner.get_task(task_id)

    def pause_task(self, task_id: str) -> bool:
        return self.runner.pause_task(task_id)

    def resume_task(self, task_id: str, *, auto_start: bool = True) -> bool:
        resumed = self.runner.resume_task(task_id)
        if resumed and auto_start:
            self.tick_once()
        return resumed

    def cancel_task(self, task_id: str) -> bool:
        return self.runner.cancel_task(task_id)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._recover_interrupted_tasks()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="pybot_admin")
        self._thread.start()
        self.tick_once()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=max(self.poll_interval * 2, 1.0))
            self._thread = None

    def close(self) -> None:
        self.stop()
        self.queue.shutdown(wait=True)

    def tick_once(self) -> list[str]:
        """Queue one execution slice for every runnable persistent task."""
        self._recover_pending_approvals()
        queued: list[str] = []
        for task in self.runner.list_tasks():
            if task.status not in (PersistentTaskStatus.PENDING, PersistentTaskStatus.RUNNING):
                continue
            if self._enqueue_step(task.task_id):
                queued.append(task.task_id)
        return queued

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        """Wait until there are no in-flight executions and no active queue jobs."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if not self._inflight and not self.queue.list_active():
                    return True
            time.sleep(0.05)
        return False

    def get_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for task in self.runner.list_tasks():
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        return {
            "tasks": counts,
            "active_queue_jobs": len(self.queue.list_active()),
            "inflight_task_ids": sorted(self._inflight),
            "capability_gap_candidates": len(self._capability_gap_candidates),
        }

    def list_capability_gap_candidates(self, *, status: str = "") -> list[dict[str, Any]]:
        candidates = [candidate.to_dict() for candidate in self._capability_gap_candidates.values()]
        if status:
            candidates = [candidate for candidate in candidates if candidate["status"] == status]
        candidates.sort(key=lambda item: (item["status"], -item["occurrences"], item["candidate_id"]))
        return candidates

    def get_capability_gap_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        candidate = self._capability_gap_candidates.get(candidate_id)
        return candidate.to_dict() if candidate is not None else None

    def draft_capability_gap_candidate(
        self,
        candidate_id: str,
        *,
        target_name: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        candidate = self._capability_gap_candidates.get(candidate_id)
        if candidate is None:
            return {"success": False, "error": "Capability gap candidate not found"}
        if candidate.draft_artifact and not overwrite:
            return {
                "success": True,
                "candidate": candidate.to_dict(),
                "draft": dict(candidate.draft_artifact),
                "already_drafted": True,
            }

        self._enrich_capability_gap_candidate(candidate)
        draft = materialize_capability_gap_draft(
            self.host_agent,
            candidate,
            target_name=target_name,
            overwrite=overwrite,
        )
        if not draft.get("success"):
            return draft

        candidate.status = "drafted"
        candidate.draft_artifact = dict(draft)
        candidate.updated_at = time.time()
        self._save_capability_gap_candidates()
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_DRAFT_CREATED,
                source="PersistentAdminRuntime",
                payload={
                    "candidate_id": candidate.candidate_id,
                    "suggested_capability_name": candidate.suggested_capability_name,
                    "asset_kind": draft.get("asset_kind", ""),
                    "draft_name": draft.get("name", ""),
                },
            )
        )
        return {
            "success": True,
            "candidate": candidate.to_dict(),
            "draft": draft,
        }

    def validate_capability_gap_candidate(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._capability_gap_candidates.get(candidate_id)
        if candidate is None:
            return {"success": False, "error": "Capability gap candidate not found"}
        if not candidate.draft_artifact:
            return {"success": False, "error": "Capability gap candidate has no draft artifact"}

        validation = validate_capability_gap_draft(self.host_agent, candidate)
        if not validation.get("success"):
            return validation

        candidate.validation_result = dict(validation)
        candidate.status = "validated" if validation.get("valid") else "validation_failed"
        candidate.updated_at = time.time()
        self._save_capability_gap_candidates()
        return {
            "success": True,
            "candidate": candidate.to_dict(),
            "validation": validation,
        }

    def publish_capability_gap_candidate(
        self,
        candidate_id: str,
        *,
        publish_to_hub: bool = False,
        hub_url: str = "",
        hub_token: str = "",
        version: str = "0.1.0",
        changelog: str = "",
    ) -> dict[str, Any]:
        candidate = self._capability_gap_candidates.get(candidate_id)
        if candidate is None:
            return {"success": False, "error": "Capability gap candidate not found"}
        if not candidate.draft_artifact:
            return {"success": False, "error": "Capability gap candidate has no draft artifact"}
        if candidate.validation_result and not candidate.validation_result.get("valid", False):
            return {"success": False, "error": "Capability gap candidate draft failed validation"}

        publish_result = publish_capability_gap_draft(
            self.host_agent,
            candidate,
            publish_to_hub=publish_to_hub,
            hub_url=hub_url,
            hub_token=hub_token,
            version=version,
            changelog=changelog,
        )
        if not publish_result.get("success"):
            return publish_result

        candidate.publish_result = dict(publish_result)
        candidate.status = "published"
        candidate.updated_at = time.time()
        self._save_capability_gap_candidates()
        return {
            "success": True,
            "candidate": candidate.to_dict(),
            "publish": publish_result,
        }

    def close_capability_gap_candidate(
        self,
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
        draft = self.draft_capability_gap_candidate(
            candidate_id,
            target_name=target_name,
            overwrite=overwrite,
        )
        if not draft.get("success"):
            return draft

        validation = self.validate_capability_gap_candidate(candidate_id)
        if not validation.get("success"):
            return validation
        if not validation["validation"].get("valid", False):
            return {
                "success": False,
                "candidate": validation["candidate"],
                "draft": draft["draft"],
                "validation": validation["validation"],
                "error": "Capability gap draft did not pass validation",
            }

        publish = self.publish_capability_gap_candidate(
            candidate_id,
            publish_to_hub=publish_to_hub,
            hub_url=hub_url,
            hub_token=hub_token,
            version=version,
            changelog=changelog,
        )
        if not publish.get("success"):
            return publish

        return {
            "success": True,
            "candidate": publish["candidate"],
            "draft": draft["draft"],
            "validation": validation["validation"],
            "publish": publish["publish"],
        }

    def start_capability_gap_rollout(
        self,
        candidate_id: str,
        *,
        strategy: str = "shadow",
        target: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        candidate = self._capability_gap_candidates.get(candidate_id)
        if candidate is None:
            return {"success": False, "error": "Capability gap candidate not found"}
        if not candidate.publish_result:
            return {"success": False, "error": "Capability gap candidate has not been published"}

        rollout = {
            "rollout_id": uuid.uuid4().hex[:12],
            "strategy": strategy.strip() or "shadow",
            "target": target.strip(),
            "note": note.strip(),
            "status": "active",
            "started_at": time.time(),
            "evaluations": [],
        }
        candidate.rollout_state = dict(rollout)
        candidate.rollout_history.append(dict(rollout))
        candidate.status = "rollout_active"
        candidate.updated_at = time.time()
        self._save_capability_gap_candidates()
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_ROLLOUT_STARTED,
                source="PersistentAdminRuntime",
                payload={
                    "candidate_id": candidate.candidate_id,
                    "suggested_capability_name": candidate.suggested_capability_name,
                    "rollout": rollout,
                },
            )
        )
        return {"success": True, "candidate": candidate.to_dict(), "rollout": rollout}

    def evaluate_capability_gap_rollout(
        self,
        candidate_id: str,
        *,
        outcome: str,
        note: str = "",
        rollout_id: str = "",
        telemetry_sample: dict[str, Any] | None = None,
        close_on_healthy: bool = True,
    ) -> dict[str, Any]:
        candidate = self._capability_gap_candidates.get(candidate_id)
        if candidate is None:
            return {"success": False, "error": "Capability gap candidate not found"}
        rollout = self._resolve_rollout(candidate, rollout_id=rollout_id)
        if rollout is None:
            return {"success": False, "error": "No rollout found for capability gap candidate"}

        normalized_outcome = outcome.strip().lower()
        evaluation = {
            "timestamp": time.time(),
            "outcome": normalized_outcome,
            "note": note.strip(),
            "telemetry_sample": dict(telemetry_sample or {}),
        }
        rollout.setdefault("evaluations", []).append(evaluation)
        rollout["updated_at"] = time.time()
        candidate.post_release_observations.append(dict(evaluation))

        if normalized_outcome in {"healthy", "verified", "adopted"}:
            rollout["status"] = "verified"
            candidate.status = "resolved" if close_on_healthy else "rollout_verified"
        elif normalized_outcome in {"regressed", "failed"}:
            rollout["status"] = "regressed"
            candidate.status = "regressed"
        else:
            rollout["status"] = "observed"
            candidate.status = "rollout_active"

        candidate.rollout_state = dict(rollout)
        candidate.updated_at = time.time()
        self._save_capability_gap_candidates()
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_ROLLOUT_EVALUATED,
                source="PersistentAdminRuntime",
                payload={
                    "candidate_id": candidate.candidate_id,
                    "suggested_capability_name": candidate.suggested_capability_name,
                    "evaluation": evaluation,
                    "rollout": rollout,
                },
            )
        )
        if candidate.status == "resolved":
            event_bus.emit(
                Event(
                    type=EventType.CAPABILITY_GAP_RESOLVED,
                    source="PersistentAdminRuntime",
                    payload={
                        "candidate_id": candidate.candidate_id,
                        "suggested_capability_name": candidate.suggested_capability_name,
                        "resolution": evaluation,
                    },
                )
            )
        return {
            "success": True,
            "candidate": candidate.to_dict(),
            "rollout": rollout,
            "evaluation": evaluation,
        }

    def promote_capability_gap_candidate(
        self,
        candidate_id: str,
        *,
        auto_start: bool = True,
    ) -> dict[str, Any]:
        candidate = self._capability_gap_candidates.get(candidate_id)
        if candidate is None:
            return {"success": False, "error": "Capability gap candidate not found"}
        if candidate.promoted_task_id:
            task = self.get_task(candidate.promoted_task_id)
            return {
                "success": True,
                "candidate": candidate.to_dict(),
                "task": task.to_dict() if task is not None else None,
                "already_promoted": True,
            }

        self._enrich_capability_gap_candidate(candidate)
        synthesis_goal = candidate.synthesis_goal or (
            "Analyze a repeated ecosystem gap and synthesize or improve a reusable capability "
            f"for {candidate.suggested_capability_name}."
        )
        recommended_steps = candidate.recommended_steps or [
            f"Analyze the recurring gap from {candidate.source}",
            f"Design or refine capability {candidate.suggested_capability_name}",
            "Validate the capability against recent failure samples",
            "Prepare rollout notes and follow-up recommendations",
        ]
        task = self.submit_goal(
            name=f"synthesize_{candidate.suggested_capability_name}",
            description=synthesis_goal,
            steps=recommended_steps,
            context={
                "capability_gap_candidate": candidate.to_dict(),
                "capability_gap_source": candidate.source,
                "capability_gap_type": candidate.gap_type,
                "capability_gap_samples": list(candidate.samples),
                "capability_gap_blueprint": {
                    "recommended_asset_kind": candidate.recommended_asset_kind,
                    "recommended_publish_target": candidate.recommended_publish_target,
                    "draft_contract": dict(candidate.draft_contract),
                    "provider_matches": [dict(item) for item in candidate.provider_matches],
                    "rollout_recommendations": list(candidate.rollout_recommendations),
                },
                "recommended_asset_kind": candidate.recommended_asset_kind,
                "recommended_publish_target": candidate.recommended_publish_target,
                "draft_contract": dict(candidate.draft_contract),
                "existing_provider_matches": [dict(item) for item in candidate.provider_matches],
                "rollout_recommendations": list(candidate.rollout_recommendations),
                "synthesis_goal": synthesis_goal,
            },
            auto_start=auto_start,
            auto_plan=False,
        )
        candidate.status = "promoted"
        candidate.promoted_task_id = task.task_id
        candidate.updated_at = time.time()
        self._save_capability_gap_candidates()
        event_bus.emit(
            Event(
                type=EventType.CAPABILITY_GAP_PROMOTED,
                source="PersistentAdminRuntime",
                payload={
                    "candidate_id": candidate.candidate_id,
                    "suggested_capability_name": candidate.suggested_capability_name,
                    "recommended_asset_kind": candidate.recommended_asset_kind,
                    "recommended_publish_target": candidate.recommended_publish_target,
                    "task_id": task.task_id,
                },
            )
        )
        return {
            "success": True,
            "candidate": candidate.to_dict(),
            "task": task.to_dict(),
        }

    def replan_task(
        self,
        task_id: str,
        *,
        reason: str = "",
        replacement_steps: list[str] | None = None,
    ) -> AdminPlan | None:
        """Replan the remaining steps for an existing durable task."""
        task = self.runner.get_task(task_id)
        if task is None:
            return None

        pending_steps = [step.description for step in task.steps if step.status == "pending"]
        completed_steps = [step.description for step in task.steps if step.status == "completed"]

        if replacement_steps:
            plan = AdminPlan(
                summary=task.context.get("plan_summary", task.description),
                steps=[str(step).strip() for step in replacement_steps if str(step).strip()],
                success_criteria=list(task.context.get("success_criteria", [])),
                planning_notes=f"Runtime replacement triggered: {reason}".strip(),
            )
        else:
            replanning_context = dict(task.context)
            replanning_context["replan_reason"] = reason
            replanning_context["completed_steps"] = completed_steps
            replanning_context["remaining_steps"] = pending_steps
            plan = self.plan_goal(
                name=task.name,
                description=task.description,
                context=replanning_context,
            )

        if not plan.steps:
            return plan

        self.runner.replace_pending_steps(task_id, plan.steps)
        self.runner.merge_context(
            task_id,
            {
                "admin_plan": plan.model_dump(),
                "plan_summary": plan.summary,
                "success_criteria": list(plan.success_criteria),
                "planning_notes": plan.planning_notes,
                "last_replan_reason": reason,
            },
        )
        return plan

    def _get_default_planner(self) -> AdminPlanner | None:
        if self._planner is not None:
            return self._planner
        llm = getattr(self.host_agent, "llm", None)
        if llm is None:
            return None
        self._planner = AdminPlanner(llm)
        return self._planner

    def _recover_interrupted_tasks(self) -> None:
        """Resume tasks that were RUNNING when the process last exited."""
        resumable = self.runner.get_resumable_tasks()
        if not resumable:
            return
        import logging

        logger = logging.getLogger(__name__)
        for task in resumable:
            if task.status == PersistentTaskStatus.RUNNING:
                logger.info("Recovering interrupted task: %s (%s)", task.name, task.task_id)
            elif self._pending_approval_info(task) is not None:
                logger.info("Recovering approval-paused task: %s (%s)", task.name, task.task_id)

    def _run_loop(self) -> None:
        while self._running:
            self.tick_once()
            time.sleep(self.poll_interval)

    def _enqueue_step(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._inflight:
                return False
            self._inflight.add(task_id)

        handle = self.queue.submit(
            self._run_task_slice,
            task_id,
            name=f"admin_step:{task_id}",
            metadata={"durable_task_id": task_id},
        )
        with self._lock:
            self._last_queue_task_ids[task_id] = handle.task_id
        task = self.runner.get_task(task_id)
        if task is not None:
            event_bus.emit(
                Event(
                    type=EventType.SCHEDULE_RUN,
                    source="PersistentAdminRuntime",
                    session_id=str(getattr(self.host_agent, "thread_id", "")).strip() or None,
                    payload={
                        "run_kind": "durable_task",
                        "phase": "queued",
                        "task_id": task.task_id,
                        "task_name": task.name,
                        "task_status": task.status.value,
                        "queue_task_id": handle.task_id,
                        "root_mode": normalize_root_mode(getattr(self.host_agent, "root_mode", "admin")),
                    },
                )
            )
        return True

    def _run_task_slice(self, task_id: str) -> dict[str, Any]:
        try:
            task = self.runner.get_task(task_id)
            if task is None:
                return {"success": False, "error": "task_not_found", "task_id": task_id}
            event_bus.emit(
                Event(
                    type=EventType.SCHEDULE_RUN,
                    source="PersistentAdminRuntime",
                    session_id=str(getattr(self.host_agent, "thread_id", "")).strip() or None,
                    payload={
                        "run_kind": "durable_task",
                        "phase": "started",
                        "task_id": task.task_id,
                        "task_name": task.name,
                        "task_status": task.status.value,
                        "root_mode": normalize_root_mode(getattr(self.host_agent, "root_mode", "admin")),
                    },
                )
            )

            def _execute(step: PersistentTaskStep, ctx: dict[str, Any]) -> dict[str, Any]:
                current_task = self.runner.get_task(task_id) or task
                raw_output = self._normalize_step_output(self._step_executor(current_task, step, ctx))
                context_update = self._memory.build_context_update(
                    task_name=current_task.name,
                    step_id=step.step_id,
                    step_description=step.description,
                    raw_output=raw_output,
                    current_context=ctx,
                )
                return {
                    "__step_output__": raw_output,
                    "__context_update__": context_update,
                }

            executed_step = self.runner.execute_step(task_id, _execute)
            if executed_step is not None and executed_step.status == "completed":
                self._maybe_replan(task_id, executed_step.output_data)
            updated = self.runner.get_task(task_id)
            if updated is not None:
                active_step = updated.current_step
                event_bus.emit(
                    Event(
                        type=EventType.SCHEDULE_RUN,
                        source="PersistentAdminRuntime",
                        session_id=str(getattr(self.host_agent, "thread_id", "")).strip() or None,
                        payload={
                            "run_kind": "durable_task",
                            "phase": "updated",
                            "task_id": updated.task_id,
                            "task_name": updated.name,
                            "task_status": updated.status.value,
                            "step_id": active_step.step_id if active_step is not None else "",
                            "step_description": active_step.description if active_step is not None else "",
                            "preview": str(updated.final_output or updated.error or ""),
                            "root_mode": normalize_root_mode(getattr(self.host_agent, "root_mode", "admin")),
                        },
                    )
                )
            return {
                "success": True,
                "task": updated.to_dict() if updated is not None else None,
            }
        finally:
            with self._lock:
                self._inflight.discard(task_id)
                self._last_queue_task_ids.pop(task_id, None)

    def _default_step_executor(
        self,
        task: PersistentTask,
        step: PersistentTaskStep,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if self.host_agent is None or not hasattr(self.host_agent, "chat"):
            raise RuntimeError("Admin runtime requires a host agent with chat()")

        prompt_context = self._memory.build_prompt_context(context)
        response = self.host_agent.chat(
            build_admin_step_prompt(
                task=task,
                step=step,
                context=prompt_context,
                root_mode=getattr(self.host_agent, "root_mode", "admin"),
            )
        )
        return {
            "step_response": response,
            "last_step_id": step.step_id,
            "last_step_description": step.description,
        }

    def _default_agent_name(self) -> str:
        mode = normalize_root_mode(getattr(self.host_agent, "root_mode", "assistant"))
        if mode == "app_matrix":
            return "root_app_matrix"
        if mode == "admin":
            return "root_admin"
        return "assistant_companion"

    @staticmethod
    def _normalize_step_output(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return result
        return {"step_response": result}

    @staticmethod
    def _normalize_plan_output(
        result: AdminPlan | dict[str, Any] | list[str] | Any,
        *,
        name: str,
        description: str,
    ) -> AdminPlan:
        if isinstance(result, AdminPlan):
            return result
        if isinstance(result, list):
            cleaned = [str(item).strip() for item in result if str(item).strip()]
            return AdminPlan(
                summary=description.strip() or name.strip() or "执行该目标",
                steps=cleaned or [description.strip() or name.strip() or "执行该目标"],
                success_criteria=["完成目标并产出可复用结果"],
            )
        if isinstance(result, dict):
            return AdminPlan.model_validate(result)
        return fallback_admin_plan(
            name=name,
            description=description,
            error=f"unsupported planner output: {type(result).__name__}",
        )

    def _maybe_replan(self, task_id: str, step_output: Any) -> None:
        if not isinstance(step_output, dict):
            return

        replacement_steps = step_output.get("replacement_steps") or step_output.get("next_steps")
        if isinstance(replacement_steps, str):
            replacement_steps = [replacement_steps]
        if replacement_steps:
            self.replan_task(
                task_id,
                reason=str(step_output.get("replan_reason", "runtime_replacement")),
                replacement_steps=list(replacement_steps),
            )
            return

        if step_output.get("replan_required"):
            self.replan_task(
                task_id,
                reason=str(step_output.get("replan_reason", "runtime_replan_requested")),
            )

    def _recover_pending_approvals(self) -> None:
        if self._approval_queue is None:
            return
        for task in self.runner.list_tasks(status=PersistentTaskStatus.PAUSED):
            pending = self._pending_approval_info(task)
            if pending is None:
                continue
            request = self._approval_queue.get_request(pending["approval_id"])
            if request is None or request.status == "pending":
                continue
            self._apply_pending_approval(task, request)

    def _on_capability_gap_detected(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, dict):
            return
        candidate = CapabilityGapCandidate.from_event_payload(payload)
        existing = self._capability_gap_candidates.get(candidate.candidate_id)
        if existing is None:
            self._capability_gap_candidates[candidate.candidate_id] = candidate
        else:
            existing.occurrences = max(existing.occurrences, candidate.occurrences)
            existing.samples = list((existing.samples + candidate.samples)[:5])
            existing.updated_at = time.time()
            candidate = existing
        self._enrich_capability_gap_candidate(candidate)
        self._save_capability_gap_candidates()

    def _enrich_capability_gap_candidate(self, candidate: CapabilityGapCandidate) -> None:
        recommended_asset_kind = self._recommend_asset_kind(candidate)
        recommended_publish_target = self._recommend_publish_target(recommended_asset_kind)
        provider_matches = self._lookup_provider_matches(candidate)
        candidate.recommended_asset_kind = recommended_asset_kind
        candidate.recommended_publish_target = recommended_publish_target
        candidate.provider_matches = provider_matches
        candidate.draft_contract = {
            "name": candidate.suggested_capability_name,
            "kind": recommended_asset_kind,
            "provides": [candidate.suggested_capability_name],
            "description": (
                f"Reusable {recommended_asset_kind} synthesized from recurring {candidate.gap_type} "
                f"signals emitted by {candidate.source}."
            ),
            "source_gap": {
                "source": candidate.source,
                "event_type": candidate.event_type,
                "gap_type": candidate.gap_type,
                "occurrences": candidate.occurrences,
            },
            "validation_inputs": list(candidate.samples[:3]),
        }
        candidate.recommended_steps = self._build_recommended_steps(candidate)
        candidate.rollout_recommendations = self._build_rollout_recommendations(candidate)
        candidate.synthesis_goal = (
            f"Synthesize or improve a reusable {recommended_asset_kind} named "
            f"{candidate.suggested_capability_name} for the recurring gap {candidate.gap_type} "
            f"observed in {candidate.source}."
        )
        candidate.updated_at = time.time()

    @staticmethod
    def _recommend_asset_kind(candidate: CapabilityGapCandidate) -> str:
        gap_type = candidate.gap_type.strip().lower()
        source = candidate.source.strip().lower()
        if gap_type == "latency_or_batching_gap":
            return "workflow"
        if gap_type == "auth_or_policy_gap":
            return "tool"
        if source.startswith("app:") and gap_type in {"missing_capability_gap", "general_runtime_gap"}:
            return "app"
        return "skill"

    @staticmethod
    def _recommend_publish_target(asset_kind: str) -> str:
        if asset_kind == "app":
            return "app_matrix"
        if asset_kind == "tool":
            return "shared_tool_library"
        return "capability_registry"

    def _lookup_provider_matches(self, candidate: CapabilityGapCandidate) -> list[dict[str, Any]]:
        registry = getattr(self.host_agent, "capability_registry", None)
        if registry is None:
            return []

        providers: list[dict[str, Any]] = []
        try:
            direct = registry.find_providers(candidate.suggested_capability_name)
            providers.extend(direct.get("providers", []))
        except Exception:
            pass

        if not providers:
            try:
                discovered = registry.discover(
                    query=candidate.suggested_capability_name,
                    include_marketplace=False,
                )
                providers.extend(discovered.get("local", []))
            except Exception:
                pass

        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in providers:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            layer = str(item.get("layer", "")).strip()
            if not name:
                continue
            key = (name, layer)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    "name": name,
                    "layer": layer,
                    "provides": list(item.get("provides", [])),
                }
            )
        return normalized

    def _build_recommended_steps(self, candidate: CapabilityGapCandidate) -> list[str]:
        asset_kind = candidate.recommended_asset_kind or self._recommend_asset_kind(candidate)
        base = [
            f"Analyze recurring signals from {candidate.source} and summarize the root cause for {candidate.gap_type}",
            (
                f"Design a reusable {asset_kind} named {candidate.suggested_capability_name} with a clear contract "
                "and implementation boundary"
            ),
            "Validate the proposed capability against recent failure samples and regression scenarios",
        ]
        if asset_kind == "app":
            base.append("Wire the new APP capability into the APP Matrix topology and document service grants")
        elif asset_kind == "workflow":
            base.append("Publish or register the workflow so other runtimes can discover and schedule it")
        elif asset_kind == "tool":
            base.append("Package the tool for shared runtime use and document governance/policy constraints")
        else:
            base.append("Package and publish the skill to the capability registry or marketplace if validation passes")
        base.append("Prepare rollout notes, migration guidance, and follow-up telemetry checks")
        return base

    def _build_rollout_recommendations(self, candidate: CapabilityGapCandidate) -> list[str]:
        publish_target = candidate.recommended_publish_target or self._recommend_publish_target(
            self._recommend_asset_kind(candidate)
        )
        recommendations = [
            "Replay recent failure samples before rollout to confirm the gap is actually closed.",
            (f"Publish via {publish_target} when validation succeeds."),
            "Emit a capability_published or rollout event so Admin telemetry can verify adoption.",
        ]
        if candidate.provider_matches:
            recommendations.insert(
                0,
                (
                    "Compare against existing matching providers before creating something net-new; "
                    "an upgrade may be cheaper than a fresh capability."
                ),
            )
        if candidate.recommended_asset_kind == "app":
            recommendations.append(
                "Issue APP-to-APP grants only after verifying caller identity and provider policy requirements."
            )
        return recommendations

    @staticmethod
    def _resolve_rollout(candidate: CapabilityGapCandidate, *, rollout_id: str = "") -> dict[str, Any] | None:
        if rollout_id:
            for rollout in candidate.rollout_history:
                if str(rollout.get("rollout_id", "")) == rollout_id:
                    return rollout
            return None
        if candidate.rollout_state:
            current_id = str(candidate.rollout_state.get("rollout_id", ""))
            for rollout in candidate.rollout_history:
                if str(rollout.get("rollout_id", "")) == current_id:
                    return rollout
        if candidate.rollout_history:
            return candidate.rollout_history[-1]
        return None

    def _load_capability_gap_candidates(self) -> dict[str, CapabilityGapCandidate]:
        if not self._capability_gap_path.exists():
            return {}
        try:
            import json

            raw = json.loads(self._capability_gap_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        candidates: dict[str, CapabilityGapCandidate] = {}
        for item in raw.get("candidates", []):
            if not isinstance(item, dict):
                continue
            candidate = CapabilityGapCandidate.from_dict(item)
            candidates[candidate.candidate_id] = candidate
        return candidates

    def _save_capability_gap_candidates(self) -> None:
        import json

        payload = {
            "version": "1.0",
            "saved_at": time.time(),
            "candidates": [candidate.to_dict() for candidate in self._capability_gap_candidates.values()],
        }
        self._capability_gap_path.parent.mkdir(parents=True, exist_ok=True)
        self._capability_gap_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _pending_approval_info(task: PersistentTask) -> dict[str, Any] | None:
        pending = task.context.get("pending_approval")
        if not isinstance(pending, dict):
            return None
        approval_id = str(pending.get("approval_id", "")).strip()
        step_id = str(pending.get("step_id", "")).strip()
        if not approval_id or not step_id:
            return None
        return pending

    def _materialize_approval_result(self, request: Any) -> Any:
        result: dict[str, Any] = {
            "success": True,
            "result": request.resolution_result,
        }
        rebuild = getattr(self.host_agent, "_rebuild_runtime_result_if_needed", None)
        if callable(rebuild):
            result = rebuild(
                request=request,
                result=result,
                approved=bool(request.approved),
                note=request.resolution_note,
            )
        resume_parent = getattr(self.host_agent, "_resume_parent_orchestration_if_needed", None)
        if callable(resume_parent):
            result = resume_parent(request=request, result=result)

        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return request.resolution_result

    @staticmethod
    def _approval_resolution_failed(request: Any, payload: Any) -> bool:
        if isinstance(payload, dict):
            status = str(payload.get("status", "")).strip().lower()
            if status in {"failed", "error"}:
                return True
            if payload.get("success") is False:
                return True
        return request.approved is False and payload in (None, "", {})

    def _apply_pending_approval(self, task: PersistentTask, request: Any) -> None:
        pending = self._pending_approval_info(task)
        if pending is None:
            return

        step = next((item for item in task.steps if item.step_id == pending["step_id"]), None)
        if step is None:
            task.context.pop("pending_approval", None)
            self.runner._save_task(task)
            return

        payload = self._materialize_approval_result(request)
        current_context = dict(task.context)
        current_context.pop("pending_approval", None)
        context_update = self._memory.build_context_update(
            task_name=task.name,
            step_id=step.step_id,
            step_description=step.description,
            raw_output=payload,
            current_context=current_context,
        )

        step.output_data = payload
        step.completed_at = time.time()
        task.context.pop("pending_approval", None)
        task.context["last_approval"] = {
            "approval_id": request.approval_id,
            "approved": request.approved,
            "status": request.status,
            "resolved_by": request.resolved_by,
            "note": request.resolution_note,
        }
        task.context.update(context_update)

        if self._approval_resolution_failed(request, payload):
            step.status = "failed"
            step.error = request.resolution_note or "approval_rejected"
            task.status = PersistentTaskStatus.FAILED
            task.error = step.error
        else:
            step.status = "completed"
            step.error = None
            task.error = None
            task.status = PersistentTaskStatus.RUNNING
            if task.current_step is None:
                task.status = PersistentTaskStatus.COMPLETED
                task.completed_at = time.time()
                task.final_output = payload

        task.heartbeat_at = time.time()
        self.runner._save_task(task)
        if self._approval_queue is not None:
            self._approval_queue.consume_request(request.approval_id)
