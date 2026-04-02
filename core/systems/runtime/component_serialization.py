"""Component serialization — serialize/deserialize agents, tools, teams.

Enables configuration-driven workflows and no-code platforms by
allowing any component to be exported as YAML/JSON and reconstructed
from those serialized forms.

Each serializable component implements ComponentSerializable protocol.
ComponentRegistry maps type names to their constructors for deserialization.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ComponentSerializable(Protocol):
    """Protocol for serializable components."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the component to a dict."""
        ...

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentSerializable:
        """Deserialize the component from a dict."""
        ...

    @property
    def component_type(self) -> str:
        """A unique string identifying this component type."""
        ...


@dataclass
class AgentSpec:
    """Serializable agent specification."""

    name: str
    role: str = ""
    description: str = ""
    system_prompt: str = ""
    model: str = "gemini-3-flash-preview"
    temperature: float = 0.7
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def component_type(self) -> str:
        return "agent"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = self.component_type
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSpec:
        data = {k: v for k, v in data.items() if k != "_type"}
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class ToolSpec:
    """Serializable tool specification."""

    name: str
    description: str = ""
    tool_type: str = "function"
    parameters: dict[str, Any] = field(default_factory=dict)
    cacheable: bool = True
    ttl: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def component_type(self) -> str:
        return "tool"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = self.component_type
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolSpec:
        data = {k: v for k, v in data.items() if k != "_type"}
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class TeamSpec:
    """Serializable team specification."""

    name: str
    description: str = ""
    agents: list[dict[str, Any]] = field(default_factory=list)
    selector_type: str = "round_robin"
    max_rounds: int = 5
    synthesizer_prompt: str = ""
    mode: str = "team"  # "team" or "society_of_mind"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def component_type(self) -> str:
        return "team"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = self.component_type
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamSpec:
        data = {k: v for k, v in data.items() if k != "_type"}
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class WorkflowSpec:
    """Serializable workflow specification."""

    name: str
    description: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    termination: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def component_type(self) -> str:
        return "workflow"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["_type"] = self.component_type
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowSpec:
        data = {k: v for k, v in data.items() if k != "_type"}
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


_REGISTRY: dict[str, type] = {
    "agent": AgentSpec,
    "tool": ToolSpec,
    "team": TeamSpec,
    "workflow": WorkflowSpec,
}


def register_component_type(type_name: str, cls: type) -> None:
    """Register a custom component type for deserialization."""
    _REGISTRY[type_name] = cls


def serialize_component(component: ComponentSerializable) -> dict[str, Any]:
    """Serialize a component to a dict with type marker."""
    return component.to_dict()


def deserialize_component(data: dict[str, Any]) -> Any:
    """Deserialize a component from a dict using the _type field."""
    type_name = data.get("_type")
    if type_name is None:
        raise ValueError("Missing '_type' field in serialized data")
    cls = _REGISTRY.get(type_name)
    if cls is None:
        raise ValueError(f"Unknown component type: {type_name!r}. Registered: {list(_REGISTRY)}")
    return cls.from_dict(data)


def to_json(component: ComponentSerializable, *, indent: int = 2) -> str:
    """Serialize a component to JSON string."""
    return json.dumps(component.to_dict(), ensure_ascii=False, indent=indent)


def from_json(json_str: str) -> Any:
    """Deserialize a component from JSON string."""
    data = json.loads(json_str)
    return deserialize_component(data)


def to_yaml(component: ComponentSerializable) -> str:
    """Serialize a component to YAML string."""
    try:
        import yaml

        return yaml.dump(component.to_dict(), allow_unicode=True, default_flow_style=False, sort_keys=False)
    except ImportError as exc:
        raise ImportError("PyYAML is required for YAML serialization. Install with: pip install pyyaml") from exc


def from_yaml(yaml_str: str) -> Any:
    """Deserialize a component from YAML string."""
    try:
        import yaml

        data = yaml.safe_load(yaml_str)
        return deserialize_component(data)
    except ImportError as exc:
        raise ImportError("PyYAML is required for YAML deserialization. Install with: pip install pyyaml") from exc


def export_components(components: list[ComponentSerializable]) -> list[dict[str, Any]]:
    """Export multiple components to a list of dicts."""
    return [serialize_component(c) for c in components]


def import_components(data_list: list[dict[str, Any]]) -> list[Any]:
    """Import multiple components from a list of dicts."""
    return [deserialize_component(d) for d in data_list]
