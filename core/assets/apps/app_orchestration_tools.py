"""Agent-callable tools for the App Orchestration Registry."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from .app_orchestration import AppOrchestrationRegistry, NodeType


class OrchQueryInput(BaseModel):
    action: str = Field(
        description=(
            "操作类型: "
            "topology - 查看完整编排拓扑; "
            "list_nodes - 列出节点(可按 node_type/domain 过滤); "
            "node_summary - 查看单个节点的上下游(需提供 node_id); "
            "register_node - 注册节点(需提供 name, node_type); "
            "add_binding - 添加绑定(需提供 source, target); "
            "remove_binding - 删除绑定; "
            "validate - 验证图完整性; "
            "register_pipeline - 注册pipeline(需提供 name, steps); "
            "list_pipelines - 列出pipeline"
        )
    )
    node_id: str = Field(default="", description="节点ID（用于 node_summary / add_binding）")
    name: str = Field(default="", description="节点或pipeline名称")
    node_type: str = Field(default="app", description="节点类型: app/workflow/agent/tool/external")
    domain: str = Field(default="", description="业务域标签")
    description: str = Field(default="", description="描述")
    source: str = Field(default="", description="绑定源节点ID")
    target: str = Field(default="", description="绑定目标节点ID")
    source_port: str = Field(default="default", description="源端口")
    target_port: str = Field(default="default", description="目标端口")
    steps: str = Field(default="", description="Pipeline步骤，逗号分隔的节点ID列表")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class AppOrchestrationTool(BaseTool):
    name: str = "app_orchestration"
    description: str = (
        "APP 编排注册表 — 管理应用间的编排关系。"
        "可以注册节点(app/workflow/agent/tool/external)、添加数据绑定、"
        "查看拓扑图、验证完整性、管理pipeline。"
        "这是 应用矩阵模式的核心工具，用于显式维护哪个APP负责什么、它们之间怎么串、输入输出怎么接。"
    )
    args_schema: type[BaseModel] = OrchQueryInput
    registry: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(
        self,
        action: str = "topology",
        node_id: str = "",
        name: str = "",
        node_type: str = "app",
        domain: str = "",
        description: str = "",
        source: str = "",
        target: str = "",
        source_port: str = "default",
        target_port: str = "default",
        steps: str = "",
    ) -> str:
        reg: AppOrchestrationRegistry = self.registry
        try:
            if action == "topology":
                return json.dumps(reg.get_topology(), ensure_ascii=False, indent=2)

            if action == "list_nodes":
                nt = NodeType(node_type) if node_type and node_type != "all" else None
                nodes = reg.list_nodes(node_type=nt, domain=domain or None)
                return json.dumps(
                    [n.to_dict() for n in nodes], ensure_ascii=False, indent=2
                )

            if action == "node_summary":
                if not node_id:
                    return json.dumps({"error": "请提供 node_id"}, ensure_ascii=False)
                summary = reg.get_node_summary(node_id)
                if summary is None:
                    return json.dumps({"error": f"节点 '{node_id}' 不存在"}, ensure_ascii=False)
                return json.dumps(summary, ensure_ascii=False, indent=2)

            if action == "register_node":
                if not name:
                    return json.dumps({"error": "请提供 name"}, ensure_ascii=False)
                node = reg.register_node(
                    name,
                    node_type,
                    description=description,
                    domain=domain,
                )
                return json.dumps(
                    {"success": True, "node": node.to_dict()},
                    ensure_ascii=False, indent=2,
                )

            if action == "add_binding":
                if not source or not target:
                    return json.dumps(
                        {"error": "请提供 source 和 target 节点ID"},
                        ensure_ascii=False,
                    )
                binding = reg.add_binding(
                    source, source_port, target, target_port,
                    description=description,
                )
                return json.dumps(
                    {"success": True, "binding": binding.to_dict()},
                    ensure_ascii=False, indent=2,
                )

            if action == "remove_binding":
                if not source or not target:
                    return json.dumps(
                        {"error": "请提供 source 和 target 节点ID"},
                        ensure_ascii=False,
                    )
                removed = reg.remove_binding(source, target)
                return json.dumps(
                    {"success": True, "removed": removed},
                    ensure_ascii=False,
                )

            if action == "validate":
                issues = reg.validate_graph()
                return json.dumps(
                    {"valid": len(issues) == 0, "issues": issues},
                    ensure_ascii=False, indent=2,
                )

            if action == "register_pipeline":
                if not name:
                    return json.dumps({"error": "请提供 name"}, ensure_ascii=False)
                step_list = [s.strip() for s in steps.split(",") if s.strip()] if steps else []
                pipeline = reg.register_pipeline(
                    name, step_list, description=description,
                )
                return json.dumps(
                    {"success": True, "pipeline": pipeline.to_dict()},
                    ensure_ascii=False, indent=2,
                )

            if action == "list_pipelines":
                pipelines = reg.list_pipelines()
                return json.dumps(
                    [p.to_dict() for p in pipelines],
                    ensure_ascii=False, indent=2,
                )

            return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)


def get_app_orchestration_tools(registry: AppOrchestrationRegistry) -> list[BaseTool]:
    return [AppOrchestrationTool(registry=registry)]
