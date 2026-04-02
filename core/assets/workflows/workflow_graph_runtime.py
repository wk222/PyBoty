"""Graph helpers for workflow scheduling, config resolution, and conditions."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict, deque
from typing import Any

from .workflow_models import BRANCH_NODE_TYPES, EdgeState, FlowEdge, NodeStatus, WorkflowDef


def _last_completed_output(workflow: WorkflowDef) -> Any:
    """Return the output of the most recently completed node."""
    latest: Any = None
    latest_ts: float = 0.0
    for node in workflow.nodes.values():
        if node.status == NodeStatus.COMPLETED and (node.completed_at or 0) > latest_ts:
            latest_ts = node.completed_at or 0
            latest = node.output
    return latest


class WorkflowGraphRuntime:
    """Provide graph traversal and variable-resolution helpers for workflows."""

    def get_adjacency(self, workflow: WorkflowDef) -> dict[str, list[FlowEdge]]:
        adjacency: dict[str, list[FlowEdge]] = defaultdict(list)
        for edge in workflow.edges:
            adjacency[edge.source].append(edge)
        return adjacency

    def get_in_degree(self, workflow: WorkflowDef) -> dict[str, int]:
        in_degree: dict[str, int] = defaultdict(int)
        for node_id in workflow.nodes:
            in_degree[node_id] = 0
        for edge in workflow.edges:
            in_degree[edge.target] += 1
        return in_degree

    def get_predecessors(self, workflow: WorkflowDef, node_id: str) -> list[str]:
        return [edge.source for edge in workflow.edges if edge.target == node_id]

    def get_successors(self, workflow: WorkflowDef, node_id: str) -> list[FlowEdge]:
        return [edge for edge in workflow.edges if edge.source == node_id]

    def get_incoming_edges(self, workflow: WorkflowDef, node_id: str) -> list[FlowEdge]:
        return [edge for edge in workflow.edges if edge.target == node_id]

    def topo_sort(self, workflow: WorkflowDef) -> list[str]:
        in_degree = self.get_in_degree(workflow)
        queue = deque([node_id for node_id, degree in in_degree.items() if degree == 0])
        ordered: list[str] = []
        while queue:
            node_id = queue.popleft()
            ordered.append(node_id)
            for edge in self.get_successors(workflow, node_id):
                in_degree[edge.target] -= 1
                if in_degree[edge.target] == 0:
                    queue.append(edge.target)
        if len(ordered) != len(workflow.nodes):
            raise ValueError("工作流存在循环依赖，无法执行（ForEach 请使用 foreach/iteration 节点实现）")
        return ordered

    # ── Edge State Tracking (Dify-style) ────────────────────────────

    def reset_edge_states(self, workflow: WorkflowDef) -> None:
        for edge in workflow.edges:
            edge.state = EdgeState.UNKNOWN

    def mark_edge_taken(self, edge: FlowEdge) -> None:
        edge.state = EdgeState.TAKEN

    def mark_edge_skipped(self, edge: FlowEdge) -> None:
        edge.state = EdgeState.SKIPPED

    def process_node_success(
        self, workflow: WorkflowDef, node_id: str, *, selected_target: str | None = None
    ) -> list[str]:
        """After a node completes, mark outgoing edges and return newly ready nodes."""
        node = workflow.nodes.get(node_id)
        is_branch = node is not None and node.type in BRANCH_NODE_TYPES

        outgoing = self.get_successors(workflow, node_id)
        ready: list[str] = []

        for edge in outgoing:
            if is_branch and selected_target:
                if edge.target == selected_target or edge.source_handle == selected_target:
                    self.mark_edge_taken(edge)
                else:
                    self.mark_edge_skipped(edge)
            else:
                self.mark_edge_taken(edge)

        for edge in outgoing:
            if edge.state == EdgeState.SKIPPED:
                self._propagate_skip(workflow, edge.target, node_id)
            elif edge.state == EdgeState.TAKEN:
                if self.is_node_ready_edge_based(workflow, edge.target):
                    ready.append(edge.target)

        return ready

    def _propagate_skip(self, workflow: WorkflowDef, node_id: str, from_node_id: str) -> None:
        """Recursively skip nodes that only have skipped incoming edges."""
        incoming = self.get_incoming_edges(workflow, node_id)
        has_non_skip_edge = any(e.state != EdgeState.SKIPPED for e in incoming if e.source != from_node_id)
        if has_non_skip_edge:
            return

        node = workflow.nodes.get(node_id)
        if node and node.status == NodeStatus.PENDING:
            node.status = NodeStatus.SKIPPED
            workflow.variables[f"{node_id}.status"] = "skipped"
            for edge in self.get_successors(workflow, node_id):
                self.mark_edge_skipped(edge)
                self._propagate_skip(workflow, edge.target, node_id)

    def is_node_ready_edge_based(self, workflow: WorkflowDef, node_id: str) -> bool:
        """A node is ready when all incoming edges are resolved and at least one is TAKEN."""
        incoming = self.get_incoming_edges(workflow, node_id)
        if not incoming:
            return workflow.nodes.get(node_id, None) is not None
        has_taken = False
        for edge in incoming:
            if edge.state == EdgeState.UNKNOWN:
                return False
            if edge.state == EdgeState.TAKEN:
                has_taken = True
        return has_taken

    # ── Variable Resolution ─────────────────────────────────────────

    _VAR_DOLLAR = re.compile(r"\$\{([^}]+)\}")
    _VAR_DIFY = re.compile(r"\{\{#([^#]+)#\}\}")

    def resolve_var(self, value: Any, workflow: WorkflowDef) -> Any:
        if not isinstance(value, str):
            return value

        value = self._resolve_dify_templates(value, workflow)

        matches = self._VAR_DOLLAR.findall(value)
        if not matches:
            if value.startswith("$") and value[1:] in workflow.variables:
                return workflow.variables[value[1:]]
            return value

        resolved = value
        for match in matches:
            variable_value = self._resolve_builtin_var(match, workflow)
            if variable_value is None:
                variable_value = self._deep_get(workflow.variables, match)
            if isinstance(variable_value, str):
                resolved = resolved.replace(f"${{{match}}}", variable_value)
                continue
            if resolved == f"${{{match}}}":
                return variable_value
            resolved = resolved.replace(f"${{{match}}}", json.dumps(variable_value, ensure_ascii=False))
        return resolved

    @staticmethod
    def _resolve_builtin_var(key: str, workflow: WorkflowDef) -> Any:
        """Handle built-in shorthand variables: $input, $last, env.XXX."""
        if key == "input":
            return workflow.variables.get("input", {})
        if key == "last":
            return _last_completed_output(workflow)
        if key.startswith("env."):
            return os.environ.get(key[4:], "")
        return None

    def _resolve_dify_templates(self, text: str, workflow: WorkflowDef) -> str:
        """Resolve Dify-style {{#node_id.variable_name#}} templates."""

        def replacer(match: re.Match[str]) -> str:
            selector = match.group(1)
            value = self._deep_get(workflow.variables, selector)
            if value is None:
                return match.group(0)
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False)

        return self._VAR_DIFY.sub(replacer, text)

    @staticmethod
    def _deep_get(variables: dict[str, Any], key: str) -> Any:
        """Support dotted keys: 'node_id.output.field' → nested access.

        Tries progressively longer prefixes as flat keys, then descends
        into the value for remaining parts. E.g. for 'api.output.data.name':
          1. Try 'api' → descend .output.data.name
          2. Try 'api.output' → descend .data.name
          3. Try 'api.output.data' → descend .name
          4. Try 'api.output.data.name' → direct hit
        """
        if key in variables:
            return variables[key]

        parts = key.split(".")
        for prefix_len in range(1, len(parts) + 1):
            prefix = ".".join(parts[:prefix_len])
            if prefix not in variables:
                continue
            current = variables[prefix]
            for part in parts[prefix_len:]:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    current = None
                    break
            if current is not None:
                return current

        return None

    def resolve_config(self, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for key, value in config.items():
            if isinstance(value, str):
                resolved[key] = self.resolve_var(value, workflow)
            elif isinstance(value, dict):
                resolved[key] = self.resolve_config(value, workflow)
            elif isinstance(value, list):
                resolved[key] = [self.resolve_var(item, workflow) if isinstance(item, str) else item for item in value]
            else:
                resolved[key] = value
        return resolved

    def evaluate_condition(self, condition: str, workflow: WorkflowDef) -> bool:
        resolved = self.resolve_var(condition, workflow)
        if isinstance(resolved, bool):
            return resolved
        if isinstance(resolved, str):
            lowered = resolved.strip().lower()
            if lowered in ("true", "yes", "1", "pass", "ok"):
                return True
            if lowered in ("false", "no", "0", "fail", ""):
                return False
            try:
                safe_vars = {key: value for key, value in workflow.variables.items() if not callable(value)}
                return bool(eval(resolved, {"__builtins__": {}}, safe_vars))
            except Exception:
                return bool(resolved)
        return bool(resolved)

    def all_predecessors_done(self, workflow: WorkflowDef, node_id: str) -> bool:
        for predecessor_id in self.get_predecessors(workflow, node_id):
            predecessor = workflow.nodes.get(predecessor_id)
            if predecessor is None:
                return False
            if predecessor.status not in (NodeStatus.COMPLETED, NodeStatus.SKIPPED):
                return False
        return True

    def get_ready_nodes(self, workflow: WorkflowDef) -> list[str]:
        ready_nodes: list[str] = []
        for node_id, node in workflow.nodes.items():
            if node.status == NodeStatus.PENDING and self.all_predecessors_done(workflow, node_id):
                ready_nodes.append(node_id)
        return ready_nodes
