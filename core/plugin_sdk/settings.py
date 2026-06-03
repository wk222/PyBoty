"""Per-extension settings: storage + schema validation.

Each installed extension can ship a ``settings.schema.json`` next to its
``pybot.manifest.json``.  The schema is a strict subset of JSON Schema
(types: object/string/number/integer/boolean/array, ``properties``,
``required``, ``enum``, ``default``, ``minimum``/``maximum``, ``minLength``
/``maxLength``, ``items``, ``description``, ``title``).  The lightweight
in-tree validator avoids pulling in ``jsonschema`` for what is essentially a
form-rendering contract.

Settings live at ``workspace/extensions/<id>/settings.json``.  Reads return
defaults from the schema when no overrides exist; writes validate against
the schema and dispatch ``EventType.SCHEDULE_RUN`` with
``payload["task_event"] = "settings.changed"`` so the PluginHost can fan it
out to ``@on_settings_change`` hooks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.plugin_sdk.manifest import MANIFEST_FILENAME, ManifestError, PyBotManifest

logger = logging.getLogger(__name__)


SETTINGS_FILENAME = "settings.json"
DEFAULT_SCHEMA_FILENAME = "settings.schema.json"


class SettingsError(ValueError):
    """Raised when settings fail validation or storage IO fails."""


@dataclass
class SchemaProperty:
    name: str
    type: str
    title: str = ""
    description: str = ""
    default: Any = None
    enum: list[Any] | None = None
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    items_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type, "required": self.required}
        if self.title:
            out["title"] = self.title
        if self.description:
            out["description"] = self.description
        if self.default is not None:
            out["default"] = self.default
        if self.enum is not None:
            out["enum"] = list(self.enum)
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.min_length is not None:
            out["min_length"] = self.min_length
        if self.max_length is not None:
            out["max_length"] = self.max_length
        if self.items_type is not None:
            out["items_type"] = self.items_type
        return out


@dataclass
class SettingsSchema:
    """Parsed view of one extension's ``settings.schema.json``."""

    title: str = ""
    description: str = ""
    properties: list[SchemaProperty] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SettingsSchema":
        if not isinstance(data, dict):
            raise SettingsError("schema must be a JSON object")
        if data.get("type", "object") != "object":
            raise SettingsError("only top-level type='object' is supported")

        title = str(data.get("title") or "")
        description = str(data.get("description") or "")
        required = set(data.get("required") or [])
        props_raw = data.get("properties") or {}
        if not isinstance(props_raw, dict):
            raise SettingsError("'properties' must be an object")

        properties: list[SchemaProperty] = []
        for name, body in props_raw.items():
            if not isinstance(body, dict):
                raise SettingsError(f"property {name!r} must be an object")
            ptype = str(body.get("type") or "string")
            if ptype not in {"object", "string", "number", "integer", "boolean", "array"}:
                raise SettingsError(
                    f"property {name!r} has unsupported type {ptype!r}"
                )
            items_type = None
            if ptype == "array":
                items = body.get("items") or {}
                items_type = str(items.get("type") or "string") if isinstance(items, dict) else "string"
            properties.append(
                SchemaProperty(
                    name=str(name),
                    type=ptype,
                    title=str(body.get("title") or ""),
                    description=str(body.get("description") or ""),
                    default=body.get("default"),
                    enum=list(body["enum"]) if isinstance(body.get("enum"), list) else None,
                    required=name in required,
                    minimum=_safe_number(body.get("minimum")),
                    maximum=_safe_number(body.get("maximum")),
                    min_length=_safe_int(body.get("minLength")),
                    max_length=_safe_int(body.get("maxLength")),
                    items_type=items_type,
                )
            )
        return cls(title=title, description=description, properties=properties)

    @classmethod
    def from_path(cls, path: str | Path) -> "SettingsSchema":
        p = Path(path)
        if not p.exists():
            raise SettingsError(f"schema not found: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SettingsError(f"schema is not valid JSON: {exc.msg}") from exc
        return cls.from_dict(data)

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.properties if p.default is not None}

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "properties": [p.to_dict() for p in self.properties],
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise SettingsError("settings must be an object")
        index = {p.name: p for p in self.properties}

        for prop in self.properties:
            if prop.required and prop.name not in data:
                raise SettingsError(f"missing required setting {prop.name!r}")

        cleaned: dict[str, Any] = {}
        for name, value in data.items():
            prop = index.get(name)
            if prop is None:
                # extra keys are silently dropped (forward compatibility)
                continue
            cleaned[name] = _validate_value(prop, value)
        return cleaned


# ---------------------------------------------------------------------------
# SettingsStore
# ---------------------------------------------------------------------------


class SettingsStore:
    """File-backed settings store rooted under ``workspace/extensions``."""

    def __init__(self, extensions_root: Path | str) -> None:
        self.root = Path(extensions_root).expanduser()

    def schema_for(self, extension_id: str) -> SettingsSchema | None:
        manifest_path = self.root / extension_id / MANIFEST_FILENAME
        if not manifest_path.exists():
            return None
        try:
            manifest = PyBotManifest.from_path(manifest_path)
        except ManifestError:
            return None
        schema_rel = manifest.settings_schema or DEFAULT_SCHEMA_FILENAME
        schema_path = (self.root / extension_id / schema_rel).resolve()
        if not schema_path.exists():
            return None
        return SettingsSchema.from_path(schema_path)

    def read(self, extension_id: str) -> dict[str, Any]:
        schema = self.schema_for(extension_id)
        path = self.root / extension_id / SETTINGS_FILENAME
        stored: dict[str, Any] = {}
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8")) or {}
            except json.JSONDecodeError as exc:
                raise SettingsError(
                    f"settings file is not valid JSON: {exc.msg}"
                ) from exc
        if not isinstance(stored, dict):
            raise SettingsError("settings file must contain an object")

        if schema is None:
            return stored
        merged = dict(schema.defaults())
        merged.update(stored)
        return schema.validate(merged)

    def write(self, extension_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        schema = self.schema_for(extension_id)
        validated = schema.validate(settings) if schema is not None else dict(settings)

        target = self.root / extension_id
        if not target.exists():
            raise SettingsError(f"extension {extension_id!r} is not installed")
        path = target / SETTINGS_FILENAME
        path.write_text(
            json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        from core.systems.runtime.event_bus import Event, EventType, event_bus

        event_bus.emit(
            Event(
                type=EventType.SCHEDULE_RUN,
                payload={
                    "task_event": "settings.changed",
                    "extension_id": extension_id,
                    "settings": validated,
                },
                source="plugin_sdk.settings",
            )
        )
        return validated


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_value(prop: SchemaProperty, value: Any) -> Any:
    if prop.enum is not None and value not in prop.enum:
        raise SettingsError(
            f"{prop.name!r} must be one of {prop.enum}, got {value!r}"
        )
    if prop.type == "string":
        if not isinstance(value, str):
            raise SettingsError(f"{prop.name!r} must be a string")
        if prop.min_length is not None and len(value) < prop.min_length:
            raise SettingsError(f"{prop.name!r} below minLength {prop.min_length}")
        if prop.max_length is not None and len(value) > prop.max_length:
            raise SettingsError(f"{prop.name!r} above maxLength {prop.max_length}")
        return value
    if prop.type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"{prop.name!r} must be an integer")
        if prop.minimum is not None and value < prop.minimum:
            raise SettingsError(f"{prop.name!r} below minimum {prop.minimum}")
        if prop.maximum is not None and value > prop.maximum:
            raise SettingsError(f"{prop.name!r} above maximum {prop.maximum}")
        return value
    if prop.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{prop.name!r} must be a number")
        if prop.minimum is not None and value < prop.minimum:
            raise SettingsError(f"{prop.name!r} below minimum {prop.minimum}")
        if prop.maximum is not None and value > prop.maximum:
            raise SettingsError(f"{prop.name!r} above maximum {prop.maximum}")
        return value
    if prop.type == "boolean":
        if not isinstance(value, bool):
            raise SettingsError(f"{prop.name!r} must be a boolean")
        return value
    if prop.type == "array":
        if not isinstance(value, list):
            raise SettingsError(f"{prop.name!r} must be a list")
        if prop.items_type is None:
            return value
        items_check = {
            "string": lambda v: isinstance(v, str),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
        }.get(prop.items_type)
        if items_check is not None and not all(items_check(v) for v in value):
            raise SettingsError(
                f"{prop.name!r} items must all be of type {prop.items_type}"
            )
        return value
    if prop.type == "object":
        if not isinstance(value, dict):
            raise SettingsError(f"{prop.name!r} must be an object")
        return value
    raise SettingsError(f"unsupported type for {prop.name!r}: {prop.type}")


__all__ = [
    "DEFAULT_SCHEMA_FILENAME",
    "SETTINGS_FILENAME",
    "SchemaProperty",
    "SettingsError",
    "SettingsSchema",
    "SettingsStore",
]
