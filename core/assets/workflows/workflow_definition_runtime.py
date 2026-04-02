"""Workflow parsing and definition-building helpers."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .workflow_models import (
    FlowEdge,
    FlowNode,
    NodeExceptionConfig,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowStatus,
)

_INLINE_CONFIG_KEYS = (
    "command",
    "tool",
    "args",
    "prompt",
    "code",
    "language",
    "expression",
    "condition",
    "routes",
    "default",
    "branches",
    "items",
    "body",
    "workflow",
    "input",
    "operation",
    "data",
    "key",
    "template",
    "sources",
    "seconds",
    "output",
    "strategy",
    "true_branch",
    "false_branch",
    "retries",
    "retry_delay",
    "timeout",
    "cwd",
    "ignore_errors",
    "continue_on_error",
    "max_items",
    "agent_name",
    "task",
    "context",
    "retry_on_fail",
    "topic",
    "agent_a",
    "agent_b",
    "judge",
    "rounds",
    "question",
    "agents",
    "aggregator",
    "workers",
    "url",
    "method",
    "headers",
    "params",
    "body_type",
    "response_type",
    "query",
    "classes",
    "instruction",
    "assignments",
    "variable",
    "value",
    "separator",
    "reverse",
    "count",
    "start",
    "end",
    "other",
    "parameters",
    "text",
    "max_iterations",
    "break_condition",
    "parallel",
    "error_policy",
    "output_type",
)


class WorkflowDefinitionRuntime:
    """Parse workflow definitions and build runtime workflow objects."""

    def parse_workflow(self, definition: str) -> WorkflowDef:
        try:
            import yaml

            data = yaml.safe_load(definition)
        except ImportError:
            data = self.parse_simple_yaml(definition)
            if not data:
                try:
                    data = json.loads(definition)
                except json.JSONDecodeError as exc:
                    raise ValueError("无法解析工作流定义（需要 Workflow Spec 或 JSON 格式）") from exc
        except Exception:
            try:
                data = json.loads(definition)
            except json.JSONDecodeError as exc:
                data = self.parse_simple_yaml(definition)
                if not data:
                    raise ValueError("无法解析工作流定义") from exc

        return self.build_workflow(data)

    def parse_simple_yaml(self, content: str) -> dict[str, Any] | None:
        result: dict[str, Any] = {}
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        current_node: dict[str, Any] | None = None
        current_edge: dict[str, Any] | None = None
        section = None
        multiline_key: str | None = None
        multiline_target: dict[str, Any] | None = None
        multiline_lines: list[str] = []
        multiline_indent: int = 0

        def _flush_multiline() -> None:
            nonlocal multiline_key, multiline_target, multiline_lines, multiline_indent
            if multiline_key and multiline_target is not None:
                multiline_target[multiline_key] = "\n".join(multiline_lines)
            multiline_key = None
            multiline_target = None
            multiline_lines = []
            multiline_indent = 0

        lines = content.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()

            if multiline_key is not None:
                raw_indent = len(line) - len(line.lstrip())
                if stripped and raw_indent >= multiline_indent:
                    multiline_lines.append(line[multiline_indent:])
                    continue
                elif not stripped:
                    multiline_lines.append("")
                    continue
                else:
                    _flush_multiline()

            if not stripped or stripped.startswith("#"):
                continue

            if stripped == "nodes:":
                section = "nodes"
                continue
            if stripped == "edges:":
                if current_node:
                    nodes.append(current_node)
                    current_node = None
                section = "edges"
                continue

            if section is None:
                if ":" in stripped:
                    key, val = stripped.split(":", 1)
                    result[key.strip()] = val.strip().strip('"').strip("'")
                continue

            if section == "nodes":
                if stripped.startswith("- id:"):
                    if current_node:
                        nodes.append(current_node)
                    current_node = {"id": stripped.split(":", 1)[1].strip().strip('"').strip("'")}
                    continue
                if current_node and ":" in stripped:
                    key, val = stripped.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    if val in ("|", "|+", "|-", ">", ">+", ">-"):
                        raw_indent = len(line) - len(line.lstrip())
                        multiline_key = key
                        multiline_target = current_node
                        multiline_lines = []
                        multiline_indent = raw_indent + 2
                        continue
                    val = val.strip('"').strip("'")
                    if val.lower() == "true":
                        val = True
                    elif val.lower() == "false":
                        val = False
                    current_node[key] = val
                continue

            if stripped.startswith("- source:"):
                if current_edge:
                    edges.append(current_edge)
                current_edge = {"source": stripped.split(":", 1)[1].strip().strip('"').strip("'")}
            elif current_edge and ":" in stripped:
                key, val = stripped.split(":", 1)
                current_edge[key.strip()] = val.strip().strip('"').strip("'")

        _flush_multiline()
        if current_node:
            nodes.append(current_node)
        if current_edge:
            edges.append(current_edge)

        result["nodes"] = nodes
        result["edges"] = edges
        return result if nodes else None

    def build_workflow(self, data: dict[str, Any]) -> WorkflowDef:
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        workflow = WorkflowDef(
            id=data.get("id", uuid.uuid4().hex[:12]),
            name=data.get("name", "未命名工作流"),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", "PyBot"),
            tags=data.get("tags", []),
            schedule=data.get("schedule"),
            variables=dict(data.get("variables", {})),
            input_schema=dict(data.get("input_schema", {})),
            output_mapping=data.get("output_mapping", None),
            status=WorkflowStatus(data.get("status", WorkflowStatus.DRAFT.value)),
            created_at=created_at if isinstance(created_at, (int, float)) else time.time(),
            updated_at=updated_at if isinstance(updated_at, (int, float)) else time.time(),
            resume_token=data.get("resume_token"),
            execution_log=list(data.get("execution_log", [])),
        )

        if isinstance(workflow.tags, str):
            workflow.tags = [tag.strip() for tag in workflow.tags.split(",")]

        raw_nodes_data = data.get("nodes", [])
        if isinstance(raw_nodes_data, dict):
            raw_nodes = []
            for node_id, node_data in raw_nodes_data.items():
                if isinstance(node_data, dict):
                    normalized = dict(node_data)
                    normalized.setdefault("id", node_id)
                    raw_nodes.append(normalized)
        elif isinstance(raw_nodes_data, list):
            raw_nodes = list(raw_nodes_data)
        else:
            raw_nodes = []
        has_start = any(node.get("type") == "start" for node in raw_nodes)
        has_end = any(node.get("type") == "end" for node in raw_nodes)

        if not has_start:
            raw_nodes.insert(0, {"id": "_start", "type": "start", "label": "开始"})
        if not has_end:
            raw_nodes.append({"id": "_end", "type": "end", "label": "结束"})

        for index, raw_node in enumerate(raw_nodes):
            node_id = raw_node.get("id", f"node_{index}")
            node_type = NodeType(raw_node.get("type", "exec"))
            config = self._extract_config(raw_node)
            position = self._normalize_position(raw_node.get("position"), index)
            exc_raw = raw_node.get("exception_config", {})
            if not exc_raw:
                exc_raw = {}
                if raw_node.get("timeout"):
                    exc_raw["timeout_seconds"] = raw_node["timeout"]
                if raw_node.get("max_retries") or raw_node.get("retries"):
                    exc_raw["max_retries"] = raw_node.get("max_retries") or raw_node.get("retries", 0)
                if raw_node.get("retry_delay"):
                    exc_raw["retry_delay"] = raw_node["retry_delay"]
                if raw_node.get("on_error"):
                    exc_raw["on_error"] = raw_node["on_error"]
                if raw_node.get("fallback_output") or raw_node.get("fallback_data"):
                    exc_raw["fallback_output"] = raw_node.get("fallback_output") or raw_node.get("fallback_data")

            workflow.nodes[node_id] = FlowNode(
                id=node_id,
                type=node_type,
                label=raw_node.get("label", raw_node.get("name", node_id)),
                config=config,
                status=NodeStatus(raw_node.get("status", NodeStatus.PENDING.value)),
                output=raw_node.get("output"),
                error=raw_node.get("error"),
                started_at=raw_node.get("started_at"),
                completed_at=raw_node.get("completed_at"),
                position=position,
                retry_count=int(raw_node.get("retry_count", 0)),
                exception_config=NodeExceptionConfig.from_dict(exc_raw),
            )

        raw_edges = data.get("edges", [])
        if raw_edges:
            for index, raw_edge in enumerate(raw_edges):
                edge = FlowEdge(
                    id=raw_edge.get("id", f"edge_{index}"),
                    source=raw_edge.get("source", ""),
                    target=raw_edge.get("target", ""),
                    condition=raw_edge.get("condition", None),
                    label=raw_edge.get("label", ""),
                )
                if edge.source in workflow.nodes and edge.target in workflow.nodes:
                    workflow.edges.append(edge)
        else:
            node_ids = list(workflow.nodes.keys())
            for index in range(len(node_ids) - 1):
                workflow.edges.append(
                    FlowEdge(
                        id=f"auto_edge_{index}",
                        source=node_ids[index],
                        target=node_ids[index + 1],
                    )
                )

        return workflow

    @staticmethod
    def _extract_config(raw_node: dict[str, Any]) -> dict[str, Any]:
        config = raw_node.get("config", {})
        if config:
            return dict(config)

        extracted: dict[str, Any] = {}
        for key in _INLINE_CONFIG_KEYS:
            if key in raw_node:
                extracted[key] = raw_node[key]
        return extracted

    @staticmethod
    def _normalize_position(position: Any, index: int) -> dict[str, float]:
        default_position = {"x": 100, "y": 100 + index * 120}
        if isinstance(position, str):
            try:
                position = json.loads(position.replace("'", '"'))
            except Exception:
                return default_position
        if isinstance(position, dict):
            return position
        return default_position
