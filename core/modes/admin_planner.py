"""Planning helpers for the ultimate-agent mode."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from core.systems.runtime.structured_output import StructuredOutputError, invoke_structured


class AdminPlan(BaseModel):
    """Structured plan for a durable admin goal."""

    summary: str = Field(description="一句话概括目标和执行策略")
    steps: list[str] = Field(description="按执行顺序排列的步骤")
    success_criteria: list[str] = Field(
        default_factory=list,
        description="用于判断任务完成的成功标准",
    )
    planning_notes: str = Field(
        default="",
        description="补充说明，例如风险、依赖或优先级判断",
    )


def build_admin_planning_messages(
    *,
    name: str,
    description: str,
    context: dict[str, Any] | None = None,
) -> list[Any]:
    """Build planning messages for the ultimate-agent mode."""
    goal_name = name.strip() or "untitled_goal"
    goal_description = description.strip() or goal_name
    context_json = json.dumps(context or {}, ensure_ascii=False, indent=2, default=str)

    system = SystemMessage(
        content=(
            "你是 PyBot 的全局管理员规划器。"
            "你的任务是把长期目标拆成一组可持久执行的步骤。"
            "步骤应该明确、可执行、顺序合理，并尽量促成能力沉淀。"
            "你拥有跨智能体协作（Swarm）能力。如果任务包含可并行处理的子任务，"
            "你应该优先设计使用 `spawn_subagent` 异步启动专家并在后续步骤使用 `wait_subagent` 同步结果的模式。"
            "优先输出 2 到 8 个步骤；如果目标非常小，也至少给出 1 个步骤。"
        )
    )
    user = HumanMessage(
        content=(
            f"目标名称: {goal_name}\n"
            f"目标描述: {goal_description}\n"
            f"已有上下文:\n{context_json}\n\n"
            "请给出：\n"
            "1. 一个简洁 summary\n"
            "2. 按顺序执行的 steps\n"
            "3. 可验证的 success_criteria\n"
            "4. 可选的 planning_notes\n"
        )
    )
    return [system, user]


def fallback_admin_plan(
    *,
    name: str,
    description: str,
    error: str = "",
) -> AdminPlan:
    """Return a conservative single-step fallback plan."""
    goal_text = description.strip() or name.strip() or "处理该目标"
    note = "LLM planner unavailable; using fallback single-step plan."
    if error:
        note += f" Error: {error}"
    return AdminPlan(
        summary=goal_text,
        steps=[goal_text],
        success_criteria=["完成该目标并产出可复用结果"],
        planning_notes=note,
    )


class AdminPlanner:
    """LLM-backed planner for durable admin goals."""

    def __init__(self, llm: BaseChatModel, *, method: str = "json_mode") -> None:
        self.llm = llm
        self.method = method

    def plan_goal(
        self,
        *,
        name: str,
        description: str,
        context: dict[str, Any] | None = None,
    ) -> AdminPlan:
        messages = build_admin_planning_messages(
            name=name,
            description=description,
            context=context,
        )
        plan = invoke_structured(
            self.llm,
            messages,
            AdminPlan,
            method=self.method,
            max_retries=2,
        )
        if not plan.steps:
            raise StructuredOutputError("Admin plan returned no steps")
        return plan
