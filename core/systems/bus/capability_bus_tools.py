"""Tool surface for querying the capability bus."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from .capability_bus_models import CapabilityLayer


class CapBusQueryInput(BaseModel):
    action: str = Field(
        description="action type: stats / graph / tree / route / find / events / deps / share / get"
    )
    query: str = Field(default="", description="task query used by route guidance")
    provides: str = Field(default="", description="required capability name for route guidance")
    limit: int = Field(default=5, description="max recommended matches for route guidance")
    layer: str = Field(default="", description="filter layer: tool/skill/agent/workflow/app")
    tag: str = Field(default="", description="filter tag")
    name: str = Field(default="", description="capability name for deps/get fallbacks")
    key: str = Field(default="", description="shared-data key for share/get")
    value: str = Field(default="", description="shared-data payload for share (JSON allowed)")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CapBusTool(BaseTool):
    name: str = "capability_bus"
    description: str = (
        "Query the PyBot capability bus: inspect the tree, graph, stats, dependencies, "
        "shared context, and route guidance across tools, skills, agents, workflows, and apps."
    )
    args_schema: type[BaseModel] = CapBusQueryInput
    bus: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(
        self,
        action: str = "stats",
        query: str = "",
        provides: str = "",
        limit: int = 5,
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
            if action == "tree":
                return json.dumps(self.bus.get_tree_projection(), ensure_ascii=False, indent=2)
            if action == "route":
                return json.dumps(
                    self.bus.get_route_projection(
                        query=query or name,
                        provides=provides,
                        max_matches=limit,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            if action == "find":
                cap_layer = CapabilityLayer(layer) if layer else None
                capabilities = self.bus.find(layer=cap_layer, tag=tag or None)
                return json.dumps([capability.to_dict() for capability in capabilities], ensure_ascii=False, indent=2)
            if action == "events":
                return json.dumps(self.bus.get_recent_events(), ensure_ascii=False, indent=2)
            if action == "deps":
                if not name:
                    return json.dumps({"error": "Provide capability name (name)"}, ensure_ascii=False)
                return json.dumps(self.bus.resolve_dependencies(name), ensure_ascii=False, indent=2)
            if action == "share":
                if not key:
                    return json.dumps({"error": "Provide key"}, ensure_ascii=False)
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
                    return json.dumps({"shared_context": shared_context}, ensure_ascii=False, indent=2)
                data = self.bus.get_data(key)
                return json.dumps({"key": key, "data": data}, ensure_ascii=False, indent=2, default=str)
            return json.dumps({"error": f"unknown action: {action}"}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)


def get_capability_bus_tools(bus: Any) -> list[BaseTool]:
    return [CapBusTool(bus=bus)]
