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
    DATABASE_QUERY = "database_query"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    WAIT_SIGNAL = "wait_signal"
    DATA_SOURCE = "data_source"
    NOTIFY = "notify"
    MONITOR = "monitor"


BRANCH_NODE_TYPES = frozenset({NodeType.CONDITION, NodeType.ROUTER, NodeType.QUESTION_CLASSIFIER})


class NodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING = "waiting"


class OnErrorStrategy(str, Enum):
    STOP_WORKFLOW = "stop_workflow"
    CONTINUE_REGULAR = "continue_regular"
    CONTINUE_ERROR = "continue_error"
    NOTIFY_AND_CONTINUE = "notify_and_continue"


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
class NodeExceptionConfig:
    """Per-node exception handling policy (inspired by Coze's ExceptionConfig)."""

    timeout_seconds: float | None = None
    max_retries: int = 0
    retry_delay: float = 1.0
    max_retry_delay: float = 60.0
    on_error: OnErrorStrategy = OnErrorStrategy.STOP_WORKFLOW
    fallback_output: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.timeout_seconds is not None:
            d["timeout_seconds"] = self.timeout_seconds
        if self.max_retries > 0:
            d["max_retries"] = self.max_retries
            d["retry_delay"] = self.retry_delay
            d["max_retry_delay"] = self.max_retry_delay
        if self.on_error != OnErrorStrategy.STOP_WORKFLOW:
            d["on_error"] = self.on_error.value
        if self.fallback_output is not None:
            d["fallback_output"] = self.fallback_output
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NodeExceptionConfig:
        on_err = raw.get("on_error", "stop_workflow")
        return cls(
            timeout_seconds=raw.get("timeout_seconds") or raw.get("timeout"),
            max_retries=int(raw.get("max_retries", 0)),
            retry_delay=float(raw.get("retry_delay", 1.0)),
            max_retry_delay=float(raw.get("max_retry_delay", 60.0)),
            on_error=OnErrorStrategy(on_err) if isinstance(on_err, str) else on_err,
            fallback_output=raw.get("fallback_output") or raw.get("fallback_data"),
        )


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
    exception_config: NodeExceptionConfig = field(default_factory=NodeExceptionConfig)
    error_output: Any = None
    idempotency_key: str | None = None

    @property
    def on_error(self) -> OnErrorStrategy:
        return self.exception_config.on_error

    @property
    def max_retries(self) -> int:
        return self.exception_config.max_retries

    @property
    def retry_delay(self) -> float:
        return self.exception_config.retry_delay

    @property
    def max_retry_delay(self) -> float:
        return self.exception_config.max_retry_delay

    @property
    def timeout_seconds(self) -> float | None:
        return self.exception_config.timeout_seconds

    @property
    def fallback_output(self) -> Any:
        return self.exception_config.fallback_output

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
        exc_dict = self.exception_config.to_dict()
        if exc_dict:
            d["exception_config"] = exc_dict
        if self.error_output is not None:
            d["error_output"] = self.error_output
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

    @property
    def is_error_edge(self) -> bool:
        return self.source_handle == "error"

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
    thread_id: str = ""
    session_key: str = ""
    root_mode: str = "assistant"
    source: str = "workflow"
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
            "thread_id": self.thread_id,
            "session_key": self.session_key,
            "root_mode": self.root_mode,
            "source": self.source,
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
        from core.assets.workflows.workflow_spec import export_workflow_spec

        return export_workflow_spec(self.to_dict(runtime=True))
