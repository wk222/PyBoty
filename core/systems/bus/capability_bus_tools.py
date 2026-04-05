"""Tool surface for querying the capability bus."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from .capability_bus_models import CapabilityLayer


class CapBusQueryInput(BaseModel):
    action: str = Field(
        description="""操作类型:
- stats: 查看能力总线统计
- graph: 查看层级图谱（Tool→Skill→Agent→Workflow→App 连接关系）
- find: 查找能力（可按 layer/tag 过滤）
- events: 查看最近事件
- deps: 检查某个能力的依赖解析
- share: 向总线共享数据
- get: 从总线获取共享数据"""
    )
    layer: str = Field(description="过滤层级: tool/skill/agent/workflow/app", default="")
    tag: str = Field(description="过滤标签", default="")
    name: str = Field(description="能力名称（用于 deps/get 操作）", default="")
    key: str = Field(description="共享数据键名（用于 share/get 操作）", default="")
    value: str = Field(description="共享数据值（JSON，用于 share 操作）", default="")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CapBusTool(BaseTool):
    name: str = "capability_bus"
    description: str = """PyBot 能力总线 — 查看和管理所有层级的能力（Tool→Skill→Agent→Workflow→App）。

可以查看能力统计、层级图谱、依赖关系，以及在不同能力之间共享数据。
这是 PyBot 积木式架构的核心：每个工具、技能、智能体、工作流、应用都是一块积木，
通过能力总线实现 1+1>2 的联动效果。"""
    args_schema: type[BaseModel] = CapBusQueryInput
    bus: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(
        self,
        action: str = "stats",
        layer: str = "",
        tag: str = "",
        name: str = "",
        key: str = "",
        value: str = "",
    ) -> str:
        try:
            if action == "stats":
                return json.dumps(self.bus.get_stats(), ensure_ascii=False, indent=2)
            if action == "graph":
                return json.dumps(self.bus.get_layer_graph(), ensure_ascii=False, indent=2)
            if action == "find":
                cap_layer = CapabilityLayer(layer) if layer else None
                capabilities = self.bus.find(layer=cap_layer, tag=tag or None)
                return json.dumps([capability.to_dict() for capability in capabilities], ensure_ascii=False, indent=2)
            if action == "events":
                return json.dumps(self.bus.get_recent_events(), ensure_ascii=False, indent=2)
            if action == "deps":
                if not name:
                    return json.dumps({"error": "请提供能力名称 (name)"}, ensure_ascii=False)
                return json.dumps(self.bus.resolve_dependencies(name), ensure_ascii=False, indent=2)
            if action == "share":
                if not key:
                    return json.dumps({"error": "请提供键名 (key)"}, ensure_ascii=False)
                try:
                    resolved_value = json.loads(value) if value else None
                except json.JSONDecodeError:
                    resolved_value = value
                self.bus.share_data(key, resolved_value, source="agent")
                return json.dumps({"success": True, "key": key}, ensure_ascii=False)
            if action == "get":
                if not key:
                    context = self.bus.get_all_context()
                    shared_context = {item_key: str(item_value)[:200] for item_key, item_value in context.items()}
                    return json.dumps(
                        {"shared_context": shared_context},
                        ensure_ascii=False,
                        indent=2,
                    )
                data = self.bus.get_data(key)
                return json.dumps({"key": key, "data": data}, ensure_ascii=False, indent=2, default=str)
            return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)


def get_capability_bus_tools(bus: Any) -> list[BaseTool]:
    return [CapBusTool(bus=bus)]
