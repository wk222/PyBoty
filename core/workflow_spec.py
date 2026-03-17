"""Workflow spec helpers for PyBot's declarative workflow format."""

from __future__ import annotations

import json
import re
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

_RUNTIME_KEYS = frozenset(
    {
        "status",
        "output",
        "error",
        "started_at",
        "completed_at",
        "retry_count",
        "position",
        "resume_token",
        "created_at",
        "updated_at",
    }
)

_TOP_LEVEL_RUNTIME = frozenset(
    {
        "status",
        "resume_token",
        "created_at",
        "updated_at",
        "variables",
    }
)

_PROMOTED_CONFIG_KEYS = frozenset(
    {
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
    }
)

_ARROW_RE = re.compile(r"^\s*(\S+)\s*->\s*(\S+)(?:\s*\|\s*(.+))?\s*$")


def export_workflow_spec(definition: dict[str, Any]) -> str:
    """Render a clean workflow spec string from a runtime definition."""
    output: dict[str, Any] = {}

    for key in ("name", "description", "version", "author", "schedule"):
        if key in definition and definition[key]:
            output[key] = definition[key]

    tags = definition.get("tags", [])
    if tags:
        output["tags"] = tags

    input_schema = definition.get("input_schema")
    if input_schema:
        output["input_schema"] = input_schema

    output_mapping = definition.get("output_mapping")
    if output_mapping:
        output["output_mapping"] = output_mapping

    raw_nodes = definition.get("nodes", [])
    if isinstance(raw_nodes, dict):
        raw_nodes = list(raw_nodes.values())

    clean_nodes = []
    for node in raw_nodes:
        node_type = node.get("type", "exec")
        if node_type in ("start", "end"):
            continue

        clean_node: dict[str, Any] = {"id": node.get("id", ""), "type": node_type}
        label = node.get("label", "")
        if label and label != clean_node["id"]:
            clean_node["label"] = label

        config = node.get("config", {})
        for key, value in config.items():
            if value is not None and value != "" and value != {} and value != []:
                clean_node[key] = value

        for key in _PROMOTED_CONFIG_KEYS:
            if key in node and key not in clean_node:
                value = node[key]
                if value is not None and value != "" and value != {} and value != []:
                    clean_node[key] = value

        clean_nodes.append(clean_node)

    output["nodes"] = clean_nodes

    raw_edges = definition.get("edges", [])
    clean_edges = []
    for edge in raw_edges:
        source = edge.get("source", edge.get("from", ""))
        target = edge.get("target", edge.get("to", ""))
        if source in ("_start",) and target and clean_nodes and target == clean_nodes[0]["id"]:
            continue
        if target in ("_end",) and source and clean_nodes and source == clean_nodes[-1]["id"]:
            continue
        if not source or not target or source == "_start" or target == "_end":
            continue

        condition = edge.get("condition", edge.get("label", ""))
        arrow = f"{source} -> {target}"
        if condition:
            arrow += f" | {condition}"
        clean_edges.append(arrow)

    if clean_edges:
        output["edges"] = clean_edges

    if yaml:
        return yaml.dump(
            output,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )

    return json.dumps(output, indent=2, ensure_ascii=False)


def parse_workflow_spec(spec_text: str) -> dict[str, Any]:
    """Parse a workflow spec string into the normalized runtime definition."""
    data = _parse_text(spec_text)

    definition: dict[str, Any] = {}
    for key in ("name", "description", "version", "author", "tags", "input_schema", "output_mapping", "schedule"):
        if key in data:
            definition[key] = data[key]

    raw_nodes = data.get("nodes", [])
    if isinstance(raw_nodes, dict):
        raw_nodes = list(raw_nodes.values())

    nodes: list[dict[str, Any]] = []
    for raw_node in raw_nodes:
        if isinstance(raw_node, str):
            continue

        node: dict[str, Any] = {
            "id": raw_node.get("id", f"node_{len(nodes)}"),
            "type": raw_node.get("type", "exec"),
        }
        if "label" in raw_node:
            node["label"] = raw_node["label"]

        config: dict[str, Any] = {}
        for key, value in raw_node.items():
            if key in ("id", "type", "label") or key in _RUNTIME_KEYS:
                continue
            if key == "config" and isinstance(value, dict):
                config.update(value)
            else:
                config[key] = value
        if config:
            node["config"] = config
        nodes.append(node)

    if not any(node.get("type") == "start" for node in nodes):
        nodes.insert(0, {"id": "_start", "type": "start", "label": "开始"})
    if not any(node.get("type") == "end" for node in nodes):
        nodes.append({"id": "_end", "type": "end", "label": "结束"})

    definition["nodes"] = nodes

    raw_edges = data.get("edges", [])
    edges: list[dict[str, Any]] = []
    for index, raw_edge in enumerate(raw_edges):
        if isinstance(raw_edge, str):
            parsed = _parse_arrow(raw_edge)
            if parsed:
                edges.append(
                    {
                        "id": f"edge_{index}",
                        "source": parsed[0],
                        "target": parsed[1],
                        "condition": parsed[2],
                        "label": parsed[2] or "",
                    }
                )
            continue

        if isinstance(raw_edge, dict):
            edges.append(
                {
                    "id": raw_edge.get("id", f"edge_{index}"),
                    "source": raw_edge.get("source", raw_edge.get("from", "")),
                    "target": raw_edge.get("target", raw_edge.get("to", "")),
                    "condition": raw_edge.get("condition"),
                    "label": raw_edge.get("label", ""),
                }
            )

    if not edges and len(nodes) >= 2:
        for index in range(len(nodes) - 1):
            edges.append(
                {
                    "id": f"auto_edge_{index}",
                    "source": nodes[index]["id"],
                    "target": nodes[index + 1]["id"],
                }
            )
    elif edges:
        node_ids = {node["id"] for node in nodes}
        first_edge_source = edges[0]["source"] if edges else None
        last_edge_target = edges[-1]["target"] if edges else None

        if first_edge_source and first_edge_source != "_start" and "_start" in node_ids:
            edges.insert(0, {"id": "auto_start_edge", "source": "_start", "target": first_edge_source})
        if last_edge_target and last_edge_target != "_end" and "_end" in node_ids:
            edges.append({"id": "auto_end_edge", "source": last_edge_target, "target": "_end"})

    definition["edges"] = edges
    return definition


def strip_workflow_runtime(definition: dict[str, Any]) -> dict[str, Any]:
    """Remove runtime-only fields while preserving the workflow structure."""
    output: dict[str, Any] = {}
    for key, value in definition.items():
        if key in _TOP_LEVEL_RUNTIME or key == "id":
            continue

        if key == "nodes":
            raw_nodes = value
            if isinstance(raw_nodes, dict):
                raw_nodes = list(raw_nodes.values())

            clean_nodes = []
            for node in raw_nodes:
                clean_nodes.append(
                    {node_key: node_value for node_key, node_value in node.items() if node_key not in _RUNTIME_KEYS}
                )
            output["nodes"] = clean_nodes
        elif key == "edges":
            output["edges"] = (
                [
                    {edge_key: edge_value for edge_key, edge_value in edge.items() if edge_key not in _RUNTIME_KEYS}
                    for edge in value
                ]
                if isinstance(value, list)
                else value
            )
        else:
            output[key] = value

    return output


def _parse_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if yaml:
        try:
            data = yaml.safe_load(text)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    raise ValueError("无法解析工作流规范（需要 YAML 或 JSON 格式）")


def _parse_arrow(text: str) -> tuple[str, str, str | None] | None:
    match = _ARROW_RE.match(text.strip())
    if not match:
        return None
    source = match.group(1)
    target = match.group(2)
    condition = (match.group(3) or "").strip() or None
    return (source, target, condition)
