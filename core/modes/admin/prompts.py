"""Prompt builders for the persistent admin runtime.

Extracted from ``core/modes/admin/runtime.py`` to keep the runtime module
focused on lifecycle and orchestration logic.
"""

from __future__ import annotations

from typing import Any

from core.systems.agents.persistent_agent_runner import PersistentTask, PersistentTaskStep
from core.modes.system_model import normalize_root_mode


def build_admin_step_prompt(
    *,
    task: PersistentTask,
    step: PersistentTaskStep,
    context: dict[str, Any],
    root_mode: str = "admin",
) -> str:
    """Build the prompt used for a single admin step."""
    plan_data = context.get("admin_plan", {}) if isinstance(context, dict) else {}
    success_criteria = context.get("success_criteria", []) if isinstance(context, dict) else []
    normalized_mode = normalize_root_mode(root_mode)
    if normalized_mode == "app_matrix":
        identity = (
            "你正在作为 PyBot 的应用矩阵执行一个持久的应用级协作任务。\n\n"
            "你的重点是串联 APP、工作流、子智能体与共享能力，"
            "把跨应用的动作组织成可持续推进的调度链路。"
        )
    else:
        identity = "你正在作为 PyBot 的长期运行总控智能体执行一个持久任务。"
    return (
        f"{identity}\n\n"
        f"任务名称: {task.name}\n"
        f"任务描述: {task.description}\n"
        f"当前步骤: {step.step_id} - {step.description}\n"
        f"当前计划: {plan_data}\n"
        f"成功标准: {success_criteria}\n"
        f"已知上下文: {context}\n\n"
        "请完成当前步骤。你可以直接分析、创建工具、委派子智能体、编排工作流、串联 APP，"
        "或为后续步骤沉淀长期能力。输出本步结果，并尽量让结果可供后续步骤复用。\n\n"
        "如果你使用了 `spawn_subagent` 启动了异步专家任务，请在后续步骤中记得使用 "
        "`wait_subagent` 来获取结果。你可以同时启动多个专家以并行加速任务。"
    )
