"""
能力总线（Capability Bus）— PyBot 积木式架构的核心联动层

设计理念：
  Tool → Skill → Agent/Workflow → App
  每一层都是积木，可以自由组合。母体（PyBot）通过能力总线连接所有子实体，
  实现能力共享、数据互通、事件联动，1+1>2。

灵感来源：
- DeepAgents: 中间件栈 + 统一状态协议 + 子任务隔离回合并
- Dify: 工作流可被 App 调用
- Unix: 万物皆文件 → 我们的理念：万物皆能力（Capability）
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from .capability_bus_models import CapabilityLayer, EventType
from .capability_bus_runtime import CapabilityBusRuntime


class CapabilityBus:
    """Thin façade over the capability-bus runtime."""

    def __init__(self, workspace_dir: str = "workspace"):
        self.runtime = CapabilityBusRuntime(workspace_dir)

    def register(
        self,
        name: str,
        layer: CapabilityLayer,
        description: str = "",
        tags: list[str] | None = None,
        dependencies: list[str] | None = None,
        provides: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        registered_by: str = "",
        origin_path: str = "",
    ) -> Any:
        return self.runtime.register(
            name,
            layer,
            description=description,
            tags=tags,
            dependencies=dependencies,
            provides=provides,
            metadata=metadata,
            registered_by=registered_by,
            origin_path=origin_path,
        )

    def unregister(self, name: str) -> None:
        self.runtime.unregister(name)

    def get(self, name: str) -> Any:
        return self.runtime.get(name)

    def list_capabilities(self) -> list[Any]:
        return self.runtime.list_capabilities()

    def find(
        self,
        layer: CapabilityLayer | None = None,
        tag: str | None = None,
        provides: str | None = None,
    ) -> list[Any]:
        return self.runtime.find(layer=layer, tag=tag, provides=provides)

    def find_by_dependency(self, capability_name: str) -> list[Any]:
        return self.runtime.find_by_dependency(capability_name)

    def record_invocation(self, name: str, success: bool, duration_ms: float = 0) -> None:
        self.runtime.record_invocation(name, success, duration_ms)

    def on(self, event_type: EventType, handler: Any) -> None:
        self.runtime.on(event_type, handler)

    def off(self, event_type: EventType, handler: Any) -> None:
        self.runtime.off(event_type, handler)

    def share_context(self, key: str, value: Any, source: str = "") -> None:
        self.runtime.share_context(key, value, source=source)

    def get_context(self, key: str) -> Any:
        return self.runtime.get_context(key)

    def get_all_context(self) -> dict[str, Any]:
        return self.runtime.get_all_context()

    def share_data(self, key: str, data: Any, source: str = "", ttl_seconds: int = 0) -> None:
        self.runtime.share_data(key, data, source=source, ttl_seconds=ttl_seconds)

    def get_data(self, key: str) -> Any:
        return self.runtime.get_data(key)

    def resolve_dependencies(self, capability_name: str) -> dict[str, Any]:
        return self.runtime.resolve_dependencies(capability_name)

    def get_layer_graph(self) -> dict[str, Any]:
        return self.runtime.get_layer_graph()

    def get_stats(self) -> dict[str, Any]:
        return self.runtime.get_stats()

    def get_recent_events(self, n: int = 20) -> list[dict[str, Any]]:
        return self.runtime.get_recent_events(n)

    def save_registry(self) -> None:
        self.runtime.save_registry()

    def auto_register_tools(self, tools: list[Any]) -> None:
        self.runtime.auto_register_tools(tools)

    def auto_register_skills(self, skill_registry: Any) -> None:
        self.runtime.auto_register_skills(skill_registry)

    def auto_register_agents(self, agent_storage: Any) -> None:
        self.runtime.auto_register_agents(agent_storage)

    def auto_register_apps(self, app_manager: Any) -> None:
        self.runtime.auto_register_apps(app_manager)

    def auto_register_workflows(self, pyflow_engine: Any) -> None:
        self.runtime.auto_register_workflows(pyflow_engine)


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


def get_capability_bus_tools(bus: CapabilityBus) -> list[BaseTool]:
    return [CapBusTool(bus=bus)]
