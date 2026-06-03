from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from core.modes.admin_planner import AdminPlanner, fallback_admin_plan


def test_admin_planner_returns_structured_plan():
    mock_llm = MagicMock(spec=BaseChatModel)
    mock_llm.invoke.return_value = AIMessage(
        content=(
            '{"summary":"Build a durable weekly report flow",'
            '"steps":["Inspect current data sources","Create reporting workflow","Schedule weekly run"],'
            '"success_criteria":["Workflow runs weekly","Output is shareable"],'
            '"planning_notes":"Prefer reusable workflow artifacts"}'
        )
    )

    planner = AdminPlanner(mock_llm, method="json_mode")
    plan = planner.plan_goal(
        name="weekly_report",
        description="Generate and schedule a weekly operations report",
        context={"priority": "high"},
    )

    assert plan.summary == "Build a durable weekly report flow"
    assert plan.steps == [
        "Inspect current data sources",
        "Create reporting workflow",
        "Schedule weekly run",
    ]
    assert "Workflow runs weekly" in plan.success_criteria


def test_fallback_admin_plan_is_conservative():
    plan = fallback_admin_plan(
        name="research_goal",
        description="Research the market and produce a reusable summary",
        error="planner unavailable",
    )

    assert plan.steps == ["Research the market and produce a reusable summary"]
    assert "planner unavailable" in plan.planning_notes
