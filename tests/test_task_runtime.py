from __future__ import annotations

from core.systems.runtime.task_runtime import TaskRuntimeService


def test_task_runtime_service_tracks_tasks_and_recent_activity():
    runtime = TaskRuntimeService()
    runtime.upsert_tasks(
        [
            {"id": "t1", "content": "inspect tree", "status": "in_progress"},
            {"id": "t2", "content": "wire task runtime", "status": "pending"},
        ]
    )
    runtime.ingest_tool_runs(
        [
            {"title": "read_file", "run_id": "run-1", "status": "completed", "source": "tool_control"},
            {"title": "write_file", "run_id": "run-2", "status": "completed", "source": "tool_control"},
        ]
    )
    runtime.ingest_permission_events(
        [
            {"action": "set_mode", "mode": "plan", "timestamp": 2.0},
        ]
    )
    runtime.record_compaction_boundary(
        {
            "reason": "conversation_compaction",
            "summary": "compacted older transcript",
            "timestamp": 3.0,
        }
    )

    projection = runtime.build_projection()

    assert projection is not None
    assert [item["id"] for item in projection["tasks"]] == ["t1", "t2"]
    assert [item["kind"] for item in projection["activities"]] == ["tool_run", "tool_run", "governance", "compaction"]
    assert projection["status_counts"]["in_progress"] == 1
    assert projection["activity_counts"]["tool_run"] == 2
    assert projection["tasks"][0]["lifecycle"] == "foreground"
    assert projection["tasks"][0]["surface"] == "chat"
    assert projection["activities"][-1]["lifecycle"] == "resume"
