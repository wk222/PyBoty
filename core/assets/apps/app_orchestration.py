"""App Orchestration Registry — explicit wiring between apps, workflows, and agents.

The APP Brain needs a concrete data model to track:
  - Which app owns which responsibility (domain / capability)
  - How apps are connected (input → output bindings)
  - Which workflows and agents serve as glue between apps
  - Runtime status of each orchestration node

This module provides that registry, persisted to JSON, with query and
mutation helpers that the APP Brain and its tools can call.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class NodeType(str, Enum):
    APP = "app"
    WORKFLOW = "workflow"
    AGENT = "agent"
    TOOL = "tool"
    EXTERNAL = "external"


class BindingDirection(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"


class NodeStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    PENDING = "pending"


@dataclass
class DataBinding:
    """Describes one data channel between two orchestration nodes."""

    source_node: str
    source_port: str
    target_node: str
    target_port: str
    direction: BindingDirection = BindingDirection.INPUT
    transform: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "source_node": self.source_node,
            "source_port": self.source_port,
            "target_node": self.target_node,
            "target_port": self.target_port,
            "direction": self.direction.value,
        }
        if self.transform:
            d["transform"] = self.transform
        if self.description:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataBinding:
        return cls(
            source_node=data["source_node"],
            source_port=data.get("source_port", "default"),
            target_node=data["target_node"],
            target_port=data.get("target_port", "default"),
            direction=BindingDirection(data.get("direction", "input")),
            transform=data.get("transform", ""),
            description=data.get("description", ""),
        )


@dataclass
class OrchestrationNode:
    """One participant in the orchestration graph."""

    node_id: str
    name: str
    node_type: NodeType
    description: str = ""
    domain: str = ""
    owner: str = ""
    input_ports: list[str] = field(default_factory=lambda: ["default"])
    output_ports: list[str] = field(default_factory=lambda: ["default"])
    status: NodeStatus = NodeStatus.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "node_type": self.node_type.value,
            "description": self.description,
            "domain": self.domain,
            "owner": self.owner,
            "input_ports": self.input_ports,
            "output_ports": self.output_ports,
            "status": self.status.value,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrchestrationNode:
        return cls(
            node_id=data["node_id"],
            name=data["name"],
            node_type=NodeType(data.get("node_type", "app")),
            description=data.get("description", ""),
            domain=data.get("domain", ""),
            owner=data.get("owner", ""),
            input_ports=list(data.get("input_ports", ["default"])),
            output_ports=list(data.get("output_ports", ["default"])),
            status=NodeStatus(data.get("status", "active")),
            metadata=dict(data.get("metadata", {})),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


@dataclass
class OrchestrationPipeline:
    """A named sequence of bindings that form a logical data flow."""

    pipeline_id: str
    name: str
    description: str = ""
    steps: list[str] = field(default_factory=list)
    enabled: bool = True
    schedule: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
            "enabled": self.enabled,
            "schedule": self.schedule,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrchestrationPipeline:
        return cls(
            pipeline_id=data["pipeline_id"],
            name=data["name"],
            description=data.get("description", ""),
            steps=list(data.get("steps", [])),
            enabled=data.get("enabled", True),
            schedule=data.get("schedule", ""),
            created_at=float(data.get("created_at", time.time())),
        )


class AppOrchestrationRegistry:
    """Central registry for app orchestration topology."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._nodes: dict[str, OrchestrationNode] = {}
        self._bindings: list[DataBinding] = []
        self._pipelines: dict[str, OrchestrationPipeline] = {}
        self._storage_path: Path | None = Path(storage_path) if storage_path else None
        if self._storage_path and self._storage_path.exists():
            self._load()

    def register_node(
        self,
        name: str,
        node_type: NodeType | str,
        *,
        description: str = "",
        domain: str = "",
        owner: str = "",
        input_ports: list[str] | None = None,
        output_ports: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        node_id: str | None = None,
    ) -> OrchestrationNode:
        resolved_type = NodeType(node_type) if isinstance(node_type, str) else node_type
        nid = node_id or uuid.uuid4().hex[:12]
        node = OrchestrationNode(
            node_id=nid,
            name=name,
            node_type=resolved_type,
            description=description,
            domain=domain,
            owner=owner,
            input_ports=input_ports or ["default"],
            output_ports=output_ports or ["default"],
            metadata=metadata or {},
        )
        self._nodes[nid] = node
        self._auto_save()
        return node

    def unregister_node(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        del self._nodes[node_id]
        self._bindings = [b for b in self._bindings if b.source_node != node_id and b.target_node != node_id]
        self._auto_save()
        return True

    def get_node(self, node_id: str) -> OrchestrationNode | None:
        return self._nodes.get(node_id)

    def find_node_by_name(self, name: str) -> OrchestrationNode | None:
        for node in self._nodes.values():
            if node.name == name:
                return node
        return None

    def find_node(
        self,
        name: str,
        *,
        node_type: NodeType | str | None = None,
    ) -> OrchestrationNode | None:
        resolved_type = NodeType(node_type) if isinstance(node_type, str) else node_type
        for node in self._nodes.values():
            if node.name != name:
                continue
            if resolved_type is not None and node.node_type != resolved_type:
                continue
            return node
        return None

    def upsert_node(
        self,
        name: str,
        node_type: NodeType | str,
        *,
        description: str = "",
        domain: str = "",
        owner: str = "",
        input_ports: list[str] | None = None,
        output_ports: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrchestrationNode:
        resolved_type = NodeType(node_type) if isinstance(node_type, str) else node_type
        existing = self.find_node(name, node_type=resolved_type)
        if existing is None:
            return self.register_node(
                name,
                resolved_type,
                description=description,
                domain=domain,
                owner=owner,
                input_ports=input_ports,
                output_ports=output_ports,
                metadata=metadata,
            )

        existing.description = description
        existing.domain = domain
        existing.owner = owner
        existing.input_ports = list(input_ports or ["default"])
        existing.output_ports = list(output_ports or ["default"])
        existing.metadata = dict(metadata or {})
        existing.updated_at = time.time()
        self._auto_save()
        return existing

    def list_nodes(
        self,
        node_type: NodeType | str | None = None,
        domain: str | None = None,
        status: NodeStatus | str | None = None,
    ) -> list[OrchestrationNode]:
        result = list(self._nodes.values())
        if node_type is not None:
            nt = NodeType(node_type) if isinstance(node_type, str) else node_type
            result = [n for n in result if n.node_type == nt]
        if domain is not None:
            result = [n for n in result if n.domain == domain]
        if status is not None:
            ns = NodeStatus(status) if isinstance(status, str) else status
            result = [n for n in result if n.status == ns]
        return result

    def update_node_status(self, node_id: str, status: NodeStatus | str) -> bool:
        node = self._nodes.get(node_id)
        if node is None:
            return False
        node.status = NodeStatus(status) if isinstance(status, str) else status
        node.updated_at = time.time()
        self._auto_save()
        return True

    def update_node_metadata(self, node_id: str, **updates: Any) -> bool:
        node = self._nodes.get(node_id)
        if node is None:
            return False
        node.metadata.update(updates)
        node.updated_at = time.time()
        self._auto_save()
        return True

    def add_binding(
        self,
        source_node: str,
        source_port: str,
        target_node: str,
        target_port: str,
        *,
        direction: BindingDirection | str = BindingDirection.INPUT,
        transform: str = "",
        description: str = "",
    ) -> DataBinding:
        if source_node not in self._nodes:
            raise KeyError(f"Source node '{source_node}' not registered")
        if target_node not in self._nodes:
            raise KeyError(f"Target node '{target_node}' not registered")
        resolved_dir = BindingDirection(direction) if isinstance(direction, str) else direction
        binding = DataBinding(
            source_node=source_node,
            source_port=source_port,
            target_node=target_node,
            target_port=target_port,
            direction=resolved_dir,
            transform=transform,
            description=description,
        )
        self._bindings.append(binding)
        self._auto_save()
        return binding

    def remove_binding(self, source_node: str, target_node: str) -> int:
        before = len(self._bindings)
        self._bindings = [
            b for b in self._bindings if not (b.source_node == source_node and b.target_node == target_node)
        ]
        removed = before - len(self._bindings)
        if removed:
            self._auto_save()
        return removed

    def list_bindings(self, node_id: str | None = None) -> list[DataBinding]:
        if node_id is None:
            return list(self._bindings)
        return [b for b in self._bindings if b.source_node == node_id or b.target_node == node_id]

    def get_upstream(self, node_id: str) -> list[OrchestrationNode]:
        source_ids = {b.source_node for b in self._bindings if b.target_node == node_id}
        return [self._nodes[nid] for nid in source_ids if nid in self._nodes]

    def get_downstream(self, node_id: str) -> list[OrchestrationNode]:
        target_ids = {b.target_node for b in self._bindings if b.source_node == node_id}
        return [self._nodes[nid] for nid in target_ids if nid in self._nodes]

    def register_pipeline(
        self,
        name: str,
        steps: list[str],
        *,
        description: str = "",
        schedule: str = "",
        pipeline_id: str | None = None,
    ) -> OrchestrationPipeline:
        pid = pipeline_id or uuid.uuid4().hex[:12]
        pipeline = OrchestrationPipeline(
            pipeline_id=pid,
            name=name,
            description=description,
            steps=steps,
            schedule=schedule,
        )
        self._pipelines[pid] = pipeline
        self._auto_save()
        return pipeline

    def unregister_pipeline(self, pipeline_id: str) -> bool:
        if pipeline_id not in self._pipelines:
            return False
        del self._pipelines[pipeline_id]
        self._auto_save()
        return True

    def list_pipelines(self) -> list[OrchestrationPipeline]:
        return list(self._pipelines.values())

    def get_pipeline(self, pipeline_id: str) -> OrchestrationPipeline | None:
        return self._pipelines.get(pipeline_id)

    def get_topology(self) -> dict[str, Any]:
        """Return a serialisable snapshot of the full orchestration graph."""
        return {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "bindings": [b.to_dict() for b in self._bindings],
            "pipelines": [p.to_dict() for p in self._pipelines.values()],
            "stats": {
                "total_nodes": len(self._nodes),
                "total_bindings": len(self._bindings),
                "total_pipelines": len(self._pipelines),
                "by_type": self._count_by_type(),
                "by_domain": self._count_by_domain(),
            },
        }

    def get_node_summary(self, node_id: str) -> dict[str, Any] | None:
        node = self._nodes.get(node_id)
        if node is None:
            return None
        upstream = self.get_upstream(node_id)
        downstream = self.get_downstream(node_id)
        bindings = self.list_bindings(node_id)
        return {
            "node": node.to_dict(),
            "upstream": [n.name for n in upstream],
            "downstream": [n.name for n in downstream],
            "bindings": [b.to_dict() for b in bindings],
        }

    def validate_graph(self) -> list[str]:
        """Check the orchestration graph for common issues."""
        issues: list[str] = []
        for b in self._bindings:
            if b.source_node not in self._nodes:
                issues.append(f"Binding references missing source node: {b.source_node}")
            if b.target_node not in self._nodes:
                issues.append(f"Binding references missing target node: {b.target_node}")
            src = self._nodes.get(b.source_node)
            if src and b.source_port not in src.output_ports:
                issues.append(f"Binding port '{b.source_port}' not in output_ports of node '{src.name}'")
            tgt = self._nodes.get(b.target_node)
            if tgt and b.target_port not in tgt.input_ports:
                issues.append(f"Binding port '{b.target_port}' not in input_ports of node '{tgt.name}'")
        for p in self._pipelines.values():
            for step in p.steps:
                if step not in self._nodes:
                    issues.append(f"Pipeline '{p.name}' references missing node: {step}")
        orphans = [
            n.name
            for n in self._nodes.values()
            if not any(b.source_node == n.node_id or b.target_node == n.node_id for b in self._bindings)
        ]
        if orphans:
            issues.append(f"Orphan nodes (no bindings): {', '.join(orphans)}")
        return issues

    def _count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self._nodes.values():
            counts[node.node_type.value] = counts.get(node.node_type.value, 0) + 1
        return counts

    def _count_by_domain(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self._nodes.values():
            key = node.domain or "(none)"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _auto_save(self) -> None:
        if self._storage_path is not None:
            self.save(self._storage_path)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "nodes": {nid: n.to_dict() for nid, n in self._nodes.items()},
            "bindings": [b.to_dict() for b in self._bindings],
            "pipelines": {pid: pl.to_dict() for pid, pl in self._pipelines.items()},
        }
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for nid, ndata in raw.get("nodes", {}).items():
            ndata.setdefault("node_id", nid)
            self._nodes[nid] = OrchestrationNode.from_dict(ndata)
        for bdata in raw.get("bindings", []):
            self._bindings.append(DataBinding.from_dict(bdata))
        for pid, pdata in raw.get("pipelines", {}).items():
            pdata.setdefault("pipeline_id", pid)
            self._pipelines[pid] = OrchestrationPipeline.from_dict(pdata)

    def clear(self) -> None:
        self._nodes.clear()
        self._bindings.clear()
        self._pipelines.clear()
        self._auto_save()
