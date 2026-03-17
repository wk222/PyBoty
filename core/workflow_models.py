"""Shared workflow graph models for definitions and runtime state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    START = "start"
    END = "end"
    EXEC = "exec"
    TOOL = "tool"
    LLM = "llm"
    CODE = "code"
    AGENT = "agent"
    APPROVE = "approve"
    CONDITION = "condition"
    ROUTER = "router"
    PARALLEL = "parallel"
    FOREACH = "foreach"
    ITERATION = "iteration"
    SUBFLOW = "subflow"
    TRANSFORM = "transform"
    MERGE = "merge"
    DELAY = "delay"
    DEBATE = "debate"
    CONSENSUS = "consensus"
    SUPERVISOR = "supervisor"
    HTTP_REQUEST = "http_request"
    QUESTION_CLASSIFIER = "question_classifier"
    VARIABLE_ASSIGNER = "variable_assigner"
    LIST_OPERATOR = "list_operator"
    PARAMETER_EXTRACTOR = "parameter_extractor"


BRANCH_NODE_TYPES = frozenset({NodeType.CONDITION, NodeType.ROUTER, NodeType.QUESTION_CLASSIFIER})


class NodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING = "waiting"


class EdgeState(str, Enum):
    UNKNOWN = "unknown"
    TAKEN = "taken"
    SKIPPED = "skipped"


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass
class FlowNode:
    id: str
    type: NodeType
    label: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.PENDING
    output: Any = None
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    position: dict[str, float] = field(default_factory=lambda: {"x": 0, "y": 0})
    retry_count: int = 0
    skip_condition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "type": self.type.value,
            "label": self.label,
            "config": self.config,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "position": self.position,
            "retry_count": self.retry_count,
        }
        if self.skip_condition:
            d["skip_condition"] = self.skip_condition
        return d


@dataclass
class FlowEdge:
    id: str
    source: str
    target: str
    condition: str | None = None
    label: str = ""
    source_handle: str = "source"
    state: EdgeState = EdgeState.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "condition": self.condition,
            "label": self.label,
            "source_handle": self.source_handle,
            "state": self.state.value,
        }


@dataclass
class NodeExecutionRecord:
    """Per-node execution record (Dify-style tracking)."""

    node_id: str
    node_type: str
    status: str = "pending"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    process_data: dict[str, Any] = field(default_factory=dict)
    elapsed_time: float = 0.0
    error: str | None = None
    retry_count: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "status": self.status,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "process_data": self.process_data,
            "elapsed_time": round(self.elapsed_time, 3),
            "error": self.error,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
        }


@dataclass
class WorkflowRunRecord:
    """Workflow run history record."""

    run_id: str
    workflow_id: str
    workflow_name: str
    status: str = "running"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: Any = None
    node_executions: list[NodeExecutionRecord] = field(default_factory=list)
    total_nodes: int = 0
    completed_nodes: int = 0
    elapsed_time: float = 0.0
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "status": self.status,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "node_executions": [rec.to_dict() for rec in self.node_executions],
            "total_nodes": self.total_nodes,
            "completed_nodes": self.completed_nodes,
            "elapsed_time": round(self.elapsed_time, 3),
            "error": self.error,
            "created_at": self.created_at,
        }


@dataclass
class WorkflowDef:
    id: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = "PyBot"
    tags: list[str] = field(default_factory=list)
    schedule: str | None = None
    nodes: dict[str, FlowNode] = field(default_factory=dict)
    edges: list[FlowEdge] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_mapping: str | None = None
    status: WorkflowStatus = WorkflowStatus.DRAFT
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resume_token: str | None = None
    execution_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, *, runtime: bool = True) -> dict[str, Any]:
        if runtime:
            return {
                "id": self.id,
                "name": self.name,
                "description": self.description,
                "version": self.version,
                "author": self.author,
                "tags": self.tags,
                "schedule": self.schedule,
                "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
                "edges": [edge.to_dict() for edge in self.edges],
                "variables": {key: value for key, value in self.variables.items() if not callable(value)},
                "input_schema": self.input_schema,
                "output_mapping": self.output_mapping,
                "status": self.status.value,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "resume_token": self.resume_token,
            }

        nodes: list[dict[str, Any]] = []
        for node in self.nodes.values():
            if node.type in (NodeType.START, NodeType.END):
                continue
            serialized = {"id": node.id, "type": node.type.value}
            if node.label and node.label != node.id:
                serialized["label"] = node.label
            for key, value in node.config.items():
                if value not in (None, "", {}, []):
                    serialized[key] = value
            nodes.append(serialized)

        edges: list[str] = []
        for edge in self.edges:
            if edge.source == "_start" or edge.target == "_end":
                continue
            line = f"{edge.source} -> {edge.target}"
            if edge.condition:
                line += f" | {edge.condition}"
            edges.append(line)

        payload: dict[str, Any] = {"name": self.name, "description": self.description, "nodes": nodes}
        if self.version != "1.0.0":
            payload["version"] = self.version
        if self.tags:
            payload["tags"] = self.tags
        if self.schedule:
            payload["schedule"] = self.schedule
        if self.input_schema:
            payload["input_schema"] = self.input_schema
        if self.output_mapping:
            payload["output_mapping"] = self.output_mapping
        if edges:
            payload["edges"] = edges
        return payload

    def to_workflow_spec(self) -> str:
        from core.workflow_spec import export_workflow_spec

        return export_workflow_spec(self.to_dict(runtime=True))
