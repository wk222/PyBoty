from __future__ import annotations

from core.systems.memory.admin_memory import AdminMemoryConfig, AdminMemoryManager


def test_build_prompt_context_compacts_large_state():
    memory = AdminMemoryManager(
        config=AdminMemoryConfig(
            max_field_chars=40,
            recent_entries_limit=2,
        )
    )
    context = {
        "plan_summary": "Build an autonomous ops loop",
        "success_criteria": ["Loop survives restart"],
        "admin_plan": {
            "summary": "Ops loop",
            "steps": ["Inspect", "Build workflow", "Verify", "Deploy"],
            "success_criteria": ["Done"],
        },
        "admin_memory": {
            "summary": "Previous work summary",
            "recent_entries": [{"step_id": "step_1", "summary": "Did initial audit"}],
            "artifacts": {"workflow_id": "wf-123"},
        },
        "huge_blob": "x" * 200,
    }

    prompt_context = memory.build_prompt_context(context)

    assert prompt_context["plan_summary"] == "Build an autonomous ops loop"
    assert prompt_context["memory_summary"] == "Previous work summary"
    assert prompt_context["artifacts"]["workflow_id"] == "wf-123"
    assert prompt_context["state"]["huge_blob"].endswith("...(truncated)")


def test_build_context_update_rolls_entries_into_summary_and_limits_artifacts():
    memory = AdminMemoryManager(
        summarize_fn=lambda text: f"summary::{text[:24]}",
        config=AdminMemoryConfig(
            recent_entries_limit=2,
            max_artifacts=2,
            max_field_chars=60,
        ),
    )

    context: dict[str, object] = {}
    update_1 = memory.build_context_update(
        task_name="admin_goal",
        step_id="step_1",
        step_description="Inspect",
        raw_output={"step_response": "inspected", "report": "a" * 120},
        current_context=context,
    )
    context.update(update_1)

    update_2 = memory.build_context_update(
        task_name="admin_goal",
        step_id="step_2",
        step_description="Build",
        raw_output={"step_response": "built", "workflow_id": "wf-001"},
        current_context=context,
    )
    context.update(update_2)

    update_3 = memory.build_context_update(
        task_name="admin_goal",
        step_id="step_3",
        step_description="Verify",
        raw_output={"step_response": "verified", "artifact_url": "http://example.test/output"},
        current_context=context,
    )

    admin_memory = update_3["admin_memory"]
    assert update_3["last_step_summary"].startswith("summary::")
    assert admin_memory["summary"].startswith("summary::")
    assert len(admin_memory["recent_entries"]) == 2
    assert len(admin_memory["artifacts"]) == 2
    assert "report" not in admin_memory["artifacts"]
