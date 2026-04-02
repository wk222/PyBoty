from __future__ import annotations

import time

from core.admin_runtime import PersistentAdminRuntime
from core.approval_queue import ApprovalQueue
from core.persistent_agent_runner import PersistentTaskStatus
from core.systems.runtime.event_bus import Event, EventType


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


def test_admin_runtime_processes_persistent_task_in_background(tmp_path):
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


def test_admin_runtime_supports_custom_step_executor_and_context(tmp_path):
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


def test_admin_runtime_auto_plans_when_steps_are_omitted(tmp_path):
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


def test_admin_runtime_supports_app_matrix_mode_prompting(tmp_path):
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


def test_admin_runtime_compresses_step_context_into_memory(tmp_path):
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


def test_admin_runtime_replaces_pending_steps_from_step_output(tmp_path):
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


def test_admin_runtime_replans_with_goal_planner_when_requested(tmp_path):
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


def test_admin_runtime_pauses_when_step_waits_for_approval(tmp_path):
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


def test_admin_runtime_recovers_approved_task_after_restart(tmp_path):
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


def test_admin_runtime_collects_and_promotes_capability_gap_candidates(tmp_path):
    runtime = PersistentAdminRuntime(
        host_agent=_DummyHostAgent(root_mode="admin"),
        storage_dir=tmp_path,
        poll_interval=0.02,
        max_workers=1,
    )
    try:
        runtime._on_capability_gap_detected(  # noqa: SLF001
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


def test_admin_runtime_materializes_skill_draft_from_capability_gap(tmp_path):
    from core.assets.skills import SkillRegistry
    from core.assets.skills.skill_marketplace import SkillMarketplace
    from core.systems.bus.capability_bus import CapabilityBus
    from core.systems.bus.capability_registry import CapabilityRegistry

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
        runtime._on_capability_gap_detected(  # noqa: SLF001
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


def test_admin_runtime_can_close_loop_skill_gap_candidate(tmp_path):
    from core.assets.skills import SkillRegistry
    from core.assets.skills.skill_marketplace import SkillMarketplace
    from core.systems.bus.capability_bus import CapabilityBus
    from core.systems.bus.capability_registry import CapabilityRegistry

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
        runtime._on_capability_gap_detected(  # noqa: SLF001
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


def test_admin_runtime_can_track_rollout_and_resolve_capability_gap(tmp_path):
    from core.assets.skills import SkillRegistry
    from core.assets.skills.skill_marketplace import SkillMarketplace
    from core.systems.bus.capability_bus import CapabilityBus
    from core.systems.bus.capability_registry import CapabilityRegistry

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
        runtime._on_capability_gap_detected(  # noqa: SLF001
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
