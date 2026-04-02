"""Topology-planning helpers for APP Brain mode."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from core.structured_output import StructuredOutputError, invoke_structured


class AppMatrixBindingProposal(BaseModel):
    """Suggested data flow between two APP nodes."""

    source_app: str = Field(description="上游 APP 名称")
    target_app: str = Field(description="下游 APP 名称")
    description: str = Field(default="", description="绑定原因和用途")
    source_port: str = Field(default="default", description="上游端口")
    target_port: str = Field(default="default", description="下游端口")
    transform: str = Field(default="", description="可选的数据转换说明")


class AppMatrixPipelineProposal(BaseModel):
    """Suggested orchestration pipeline for multiple APPs."""

    name: str = Field(description="Pipeline 名称")
    steps: list[str] = Field(description="按执行顺序排列的 APP 名称")
    description: str = Field(default="", description="Pipeline 说明")
    schedule: str = Field(default="", description="可选的调度表达式")


class AppMatrixTopologyPlan(BaseModel):
    """Structured APP Brain plan for app-level orchestration."""

    summary: str = Field(description="一句话概括业务目标与编排策略")
    participating_apps: list[str] = Field(
        default_factory=list,
        description="建议参与协作的 APP 名称",
    )
    bindings: list[AppMatrixBindingProposal] = Field(
        default_factory=list,
        description="建议建立的数据流绑定",
    )
    pipelines: list[AppMatrixPipelineProposal] = Field(
        default_factory=list,
        description="建议建立的编排管线",
    )
    missing_capabilities: list[str] = Field(
        default_factory=list,
        description="现有 APP 还缺少的能力",
    )
    planning_notes: str = Field(
        default="",
        description="补充说明，例如依赖、风险、共享数据或协作边界",
    )


def build_app_matrix_planning_messages(
    *,
    goal_name: str,
    goal_description: str,
    app_inventory: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> list[Any]:
    """Build planning messages for APP Brain topology proposals."""
    apps_json = json.dumps(app_inventory or [], ensure_ascii=False, indent=2, default=str)
    context_json = json.dumps(context or {}, ensure_ascii=False, indent=2, default=str)

    system = SystemMessage(
        content=(
            "你是 PyBot 的 应用矩阵拓扑规划器。"
            "你的任务是根据业务目标，识别应该由哪些现有 APP 参与协作，"
            "它们之间应该如何传递数据，以及是否需要额外的 APP 能力。"
            "优先复用现有 APP；如果能力缺失，再把缺口写入 missing_capabilities。"
            "不要凭空编造不存在的 APP 名称。"
        )
    )
    user = HumanMessage(
        content=(
            f"目标名称: {goal_name}\n"
            f"目标描述: {goal_description}\n\n"
            f"现有 APP 清单:\n{apps_json}\n\n"
            f"附加上下文:\n{context_json}\n\n"
            "请输出：\n"
            "1. summary\n"
            "2. participating_apps\n"
            "3. bindings\n"
            "4. pipelines\n"
            "5. missing_capabilities\n"
            "6. planning_notes\n"
        )
    )
    return [system, user]


def fallback_app_matrix_plan(
    *,
    goal_name: str,
    goal_description: str,
    app_inventory: list[dict[str, Any]] | None = None,
    error: str = "",
) -> AppMatrixTopologyPlan:
    """Return a conservative APP Brain plan when no planner is available."""
    apps = [str(app.get("name", "")).strip() for app in (app_inventory or []) if str(app.get("name", "")).strip()]
    participating = apps[:3]
    pipelines: list[AppMatrixPipelineProposal] = []
    if len(participating) >= 2:
        pipelines.append(
            AppMatrixPipelineProposal(
                name=f"{goal_name.strip() or 'app_matrix'}_pipeline",
                steps=participating[:],
                description=goal_description.strip() or goal_name.strip(),
            )
        )
    note = "LLM planner unavailable; using conservative APP Brain plan."
    if error:
        note += f" Error: {error}"
    missing = [] if participating else ["No existing apps matched the goal; create a suitable app first."]
    return AppMatrixTopologyPlan(
        summary=goal_description.strip() or goal_name.strip() or "Coordinate existing apps around the goal",
        participating_apps=participating,
        bindings=[],
        pipelines=pipelines,
        missing_capabilities=missing,
        planning_notes=note,
    )


class AppMatrixPlanner:
    """LLM-backed planner for APP-level orchestration topology."""

    def __init__(self, llm: BaseChatModel, *, method: str = "json_mode") -> None:
        self.llm = llm
        self.method = method

    def plan_topology(
        self,
        *,
        goal_name: str,
        goal_description: str,
        app_inventory: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> AppMatrixTopologyPlan:
        messages = build_app_matrix_planning_messages(
            goal_name=goal_name,
            goal_description=goal_description,
            app_inventory=app_inventory,
            context=context,
        )
        plan = invoke_structured(
            self.llm,
            messages,
            AppMatrixTopologyPlan,
            method=self.method,
            max_retries=2,
        )
        if not plan.summary.strip():
            raise StructuredOutputError("APP Brain plan returned no summary")
        return plan
