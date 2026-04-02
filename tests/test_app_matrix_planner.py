from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from core.assets.apps.app_matrix_planner import AppMatrixPlanner, fallback_app_matrix_plan


def test_app_matrix_planner_returns_structured_topology_plan():
    mock_llm = MagicMock(spec=BaseChatModel)
    mock_llm.invoke.return_value = AIMessage(
        content=(
            '{"summary":"Use CRM and Audit apps to coordinate customer follow-up and compliance checks",'
            '"participating_apps":["crm","audit"],'
            '"bindings":[{"source_app":"crm","target_app":"audit",'
            '"description":"Send customer cases for audit review"}],'
            '"pipelines":[{"name":"crm_audit_loop","steps":["crm","audit"],"description":"Review flagged customers"}],'
            '"missing_capabilities":["Shared customer risk scoring"],'
            '"planning_notes":"Prefer reusing current apps before creating a new one"}'
        )
    )

    planner = AppMatrixPlanner(mock_llm, method="json_mode")
    plan = planner.plan_topology(
        goal_name="customer_risk_loop",
        goal_description="Coordinate CRM follow-up with audit review",
        app_inventory=[
            {"name": "crm", "mode": "assistant"},
            {"name": "audit", "mode": "workflow"},
        ],
        context={"team": "ops"},
    )

    assert plan.summary.startswith("Use CRM and Audit apps")
    assert plan.participating_apps == ["crm", "audit"]
    assert plan.bindings[0].source_app == "crm"
    assert plan.pipelines[0].name == "crm_audit_loop"
    assert "Shared customer risk scoring" in plan.missing_capabilities


def test_fallback_app_matrix_plan_uses_existing_apps_when_available():
    plan = fallback_app_matrix_plan(
        goal_name="cross_app_goal",
        goal_description="Coordinate apps around a shared business goal",
        app_inventory=[
            {"name": "crm"},
            {"name": "audit"},
            {"name": "marketing"},
        ],
        error="planner unavailable",
    )

    assert plan.participating_apps == ["crm", "audit", "marketing"]
    assert plan.pipelines[0].steps == ["crm", "audit", "marketing"]
    assert "planner unavailable" in plan.planning_notes
