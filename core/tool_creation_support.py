"""Support helpers for persisting and validating dynamic tool definitions."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_storage import AgentStorage
from .tool_storage import ToolStorage


@dataclass(slots=True)
class ToolCreationError(ValueError):
    """Structured error returned by tool-creation workflows."""

    message: str
    suggestion: str | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class ToolTarget:
    """Resolved storage target for a newly created tool."""

    storage: ToolStorage
    location: str


def validate_tool_name(tool_name: str) -> None:
    """Ensure tool names stay shell- and code-friendly."""
    if not tool_name.replace("_", "").isalnum():
        raise ToolCreationError("工具名称只能包含字母、数字和下划线")


def normalize_dependencies(dependencies: list[str] | str | None) -> list[str]:
    """Normalize dependency input from JSON, CSV, or native lists."""
    if dependencies is None:
        return []

    parsed: Any = dependencies
    if isinstance(parsed, str):
        if not parsed.strip():
            return []
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in parsed.split(",") if item.strip()]

    if not isinstance(parsed, list):
        raise ToolCreationError("依赖定义必须是字符串列表")

    return [str(item).strip() for item in parsed if str(item).strip()]


def parse_parameter_definitions(parameters: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse and normalize tool parameter definitions."""
    parsed: Any = parameters
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise ToolCreationError(f"参数定义格式错误: {exc}") from exc

    if not isinstance(parsed, list):
        raise ToolCreationError("参数定义必须是数组")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(parsed, 1):
        if not isinstance(item, dict):
            raise ToolCreationError(f"参数定义格式错误: 第 {index} 个参数必须是对象")

        name = str(item.get("name", "")).strip()
        if not name:
            raise ToolCreationError(f"参数定义格式错误: 第 {index} 个参数缺少 name")

        normalized_item: dict[str, Any] = {
            "name": name,
            "type": str(item.get("type", "str")),
            "description": str(item.get("description", "")),
        }
        if "default" in item:
            normalized_item["default"] = item.get("default")
        normalized.append(normalized_item)

    return normalized


def compile_tool_code(tool_name: str, code: str) -> None:
    """Fail fast on Python syntax errors before persisting a tool."""
    try:
        compile(code, f"<tool:{tool_name}>", "exec")
    except SyntaxError as exc:
        raise ToolCreationError(
            f"代码语法错误 (第{exc.lineno}行): {exc.msg}",
            suggestion=(
                f"请检查第{exc.lineno}行附近的代码，修复语法错误后重新提交。"
                "常见问题：缺少冒号、缩进不一致、括号不匹配。"
            ),
        ) from exc


def resolve_target_storage(
    storage: ToolStorage | None,
    *,
    agent_storage: AgentStorage | None = None,
    target_agent: str | None = None,
) -> ToolTarget:
    """Resolve whether the tool should live globally or inside an agent."""
    if storage is None:
        raise ToolCreationError("Storage not configured")

    if not target_agent:
        return ToolTarget(storage=storage, location="全局工具库")

    if agent_storage is None:
        raise ToolCreationError("未配置智能体存储，无法为指定智能体创建工具")

    agent_def = agent_storage.get_agent(target_agent)
    if agent_def is None:
        raise ToolCreationError(f"目标智能体 '{target_agent}' 不存在")

    tools_dir = Path(agent_storage.base_dir) / target_agent / "tools"
    return ToolTarget(storage=ToolStorage(base_dir=str(tools_dir)), location=f"智能体 '{target_agent}' 的专属工具库")


def build_tool_definition(
    *,
    tool_name: str,
    description: str,
    parameters: list[dict[str, Any]],
    code: str,
    dependencies: list[str],
    usage_guide: str,
    from_template: str | None = None,
) -> dict[str, Any]:
    """Create the persisted tool-definition payload."""
    payload: dict[str, Any] = {
        "name": tool_name,
        "description": description,
        "parameters": parameters,
        "code": code,
        "dependencies": dependencies,
        "usage_guide": usage_guide or description,
        "created_at": time.time(),
        "usage_count": 0,
    }
    if from_template:
        payload["from_template"] = from_template
    return payload


def persist_validated_tool_definition(
    storage: ToolStorage,
    tool_definition: dict[str, Any],
    *,
    validator: Callable[[dict[str, Any]], Any],
) -> None:
    """Persist a tool definition and roll back safely if validation fails."""
    tool_name = str(tool_definition["name"])
    existing_definition = storage.get_tool(tool_name)

    storage.upsert_tool(tool_name, tool_definition)
    try:
        validator(tool_definition)
    except Exception as exc:
        if existing_definition is None:
            storage.remove_tool(tool_name)
        else:
            storage.upsert_tool(tool_name, existing_definition)
        raise ToolCreationError(f"工具创建失败: {exc}") from exc
