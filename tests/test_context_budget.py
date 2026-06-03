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


def test_context_budget_micro_trim_tool_output_head_tail_behavior():
    manager = ContextBudgetManager(context_limit=128000)
    
    long_text = "A" * 1000 + "B" * 1000 + "C" * 1000  # 3000 chars
    budget = 400
    
    trimmed, removed = manager.micro_trim_tool_output(
        long_text, 
        pressure_level="high", # default is 500
        max_chars=budget
    )
    
    assert removed == 3000 - budget
    assert "A" * (budget // 2) in trimmed
    assert "C" * (budget // 4) in trimmed
    assert "B" not in trimmed # B is in the middle, should be trimmed
    assert f"[{removed} chars trimmed]" in trimmed


def test_context_budget_apply_micro_trim_based_on_pressure():
    manager = ContextBudgetManager(context_limit=1000) # small limit
    
    # Fake session record to simulate high token usage
    session_record = {
        "message_count": 100, # 100 * 150 = 15000 tokens > 1000 limit -> critical pressure
    }
    
    outputs = {
        "tool_1": "X" * 500, # 500 chars. critical limit is 200
        "tool_2": "Y" * 100  # 100 chars, < 200, shouldn't be trimmed
    }
    
    trimmed_outputs, stats = manager.apply_micro_trim(outputs, session_record=session_record)
    
    assert "tool_1" in stats
    assert stats["tool_1"] == 300 # 500 - 200 = 300 removed
    assert len(trimmed_outputs["tool_1"]) > 100
    assert "trimmed" in trimmed_outputs["tool_1"]
    
    assert "tool_2" not in stats
    assert trimmed_outputs["tool_2"] == "Y" * 100
