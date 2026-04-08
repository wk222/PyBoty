from __future__ import annotations

from core.systems.runtime.context_budget import ContextBudgetManager
from core.systems.runtime.projected_runtime_view import build_projected_runtime_view


def test_context_budget_prefers_canonical_runtime_view():
    manager = ContextBudgetManager(context_limit=200_000)
    canonical = build_projected_runtime_view(
        thread_id="thread-budget",
        root_mode="assistant",
        system_context={"working_summary": "Canonical summary from projected runtime view."},
        session={"session_notebook_summary": "note one\nnote two"},
        tasks={
            "activities": [
                {"activity_id": "a1", "kind": "tool_run", "title": "read_file", "timestamp": 1},
                {"activity_id": "a2", "kind": "governance", "title": "permission:ask", "timestamp": 2},
            ]
        },
    )

    estimated = manager.estimate_session_tokens(
        {
            "message_count": 2,
            "working_summary": "",
            "timeline": [],
            "runtime_view": canonical.to_payload(),
        }
    )

    assert estimated > 300
