"""Typed policy helpers for session and durable memory writes.

Self-contained — does not depend on any module under
``core.systems.memory`` (taxonomy was inlined when the unified
:class:`MemoryEngine` replaced the old multi-class memory subsystem).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Local taxonomy (inlined from the now-deleted memory_taxonomy module)
# ---------------------------------------------------------------------------

SESSION_MEMORY_TYPE = "session_note"

_DURABLE_TYPES: frozenset[str] = frozenset({"user", "feedback", "project", "reference"})
ALL_MEMORY_TYPES: frozenset[str] = frozenset({SESSION_MEMORY_TYPE}) | _DURABLE_TYPES

_TYPE_DEFAULT_LAYER: dict[str, str] = {
    SESSION_MEMORY_TYPE: "session",
    "user": "agent",
    "feedback": "agent",
    "project": "admin",
    "reference": "session",
}

_LAYER_ALLOWED_TYPES: dict[str, frozenset[str]] = {
    "workspace": frozenset({SESSION_MEMORY_TYPE, "reference"}),
    "session": ALL_MEMORY_TYPES,
    "agent": frozenset({"user", "feedback", SESSION_MEMORY_TYPE}),
    "admin": frozenset({"project", "reference", "feedback", SESSION_MEMORY_TYPE}),
}

ALL_LAYERS: frozenset[str] = frozenset(_LAYER_ALLOWED_TYPES)


def normalize_memory_type(memory_type: str) -> str:
    normalized = str(memory_type).strip().lower()
    return normalized if normalized in _DURABLE_TYPES else SESSION_MEMORY_TYPE


def default_layer_for_type(memory_type: str) -> str:
    normalized = normalize_memory_type(memory_type)
    return _TYPE_DEFAULT_LAYER.get(normalized, "session")


def validate_layer_for_type(memory_type: str, layer: str) -> str | None:
    """Return an error message if the layer/type combination is invalid."""
    normalized_type = normalize_memory_type(memory_type)
    normalized_layer = str(layer).strip().lower() or "session"
    if normalized_layer not in ALL_LAYERS:
        return (
            f"unknown memory layer: {normalized_layer!r}; "
            f"valid layers: {sorted(ALL_LAYERS)}"
        )
    allowed = _LAYER_ALLOWED_TYPES[normalized_layer]
    if normalized_type not in allowed:
        return (
            f"memory type {normalized_type!r} is not allowed in layer "
            f"{normalized_layer!r}; allowed types: {sorted(allowed)}"
        )
    return None


# ---------------------------------------------------------------------------
# Policy / decision shape
# ---------------------------------------------------------------------------

_ABSOLUTE_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DERIVABLE_FACT_RE = re.compile(
    r"(`[^`]+`|\.py\b|\.md\b|repo\b|repository\b|codebase\b|"
    r"function\b|class\b|module\b|workflow file\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SessionMemoryDecision:
    memory_type: str
    durable: bool
    note: str
    occurred_on: str
    verified: bool
    warnings: list[str] = field(default_factory=list)


def validate_session_memory(
    *,
    note: str,
    memory_type: str = SESSION_MEMORY_TYPE,
    durable: bool = False,
    occurred_on: str = "",
    verified: bool = False,
) -> SessionMemoryDecision:
    normalized_note = str(note).strip()
    if not normalized_note:
        raise ValueError("note is required")

    normalized_type = (
        str(memory_type or SESSION_MEMORY_TYPE).strip().lower() or SESSION_MEMORY_TYPE
    )
    if normalized_type not in ALL_MEMORY_TYPES:
        raise ValueError(f"unsupported memory_type: {normalized_type}")

    normalized_occurred_on = _resolve_occurred_on(normalized_note, occurred_on)
    normalized_durable = bool(durable)
    warnings: list[str] = []

    if normalized_type == SESSION_MEMORY_TYPE:
        if normalized_durable:
            raise ValueError("session_note memories cannot be durable")
        if verified:
            warnings.append("session_note memories ignore verification metadata")
        return SessionMemoryDecision(
            memory_type=normalized_type,
            durable=False,
            note=normalized_note,
            occurred_on=normalized_occurred_on,
            verified=False,
            warnings=warnings,
        )

    if not normalized_durable:
        raise ValueError(f"{normalized_type} memories must be stored as durable memories")

    if normalized_type == "project" and not normalized_occurred_on:
        raise ValueError("project memories require an absolute YYYY-MM-DD date")

    if normalized_type != "reference" and _DERIVABLE_FACT_RE.search(normalized_note):
        raise ValueError(
            f"{normalized_type} memory looks derivable from repository facts; "
            "store it as reference memory or keep it ephemeral"
        )

    if normalized_type == "reference" and not verified:
        warnings.append("reference memory is unverified; validate it again before recall")

    return SessionMemoryDecision(
        memory_type=normalized_type,
        durable=True,
        note=normalized_note,
        occurred_on=normalized_occurred_on,
        verified=bool(verified),
        warnings=warnings,
    )


def typed_memory_entry_payload(
    decision: SessionMemoryDecision,
    *,
    layer: str = "",
    source: str = "session_runtime",
) -> dict[str, object]:
    resolved_layer = str(layer).strip() or default_layer_for_type(decision.memory_type)
    layer_error = validate_layer_for_type(decision.memory_type, resolved_layer)
    warnings = list(decision.warnings)
    if layer_error:
        warnings.append(f"layer mismatch: {layer_error}")
    return {
        "memory_type": decision.memory_type,
        "durable": decision.durable,
        "note": decision.note,
        "occurred_on": decision.occurred_on,
        "verified": decision.verified,
        "warnings": warnings,
        "layer": resolved_layer,
        "source": str(source).strip() or "session_runtime",
    }


def _resolve_occurred_on(note: str, occurred_on: str) -> str:
    normalized = str(occurred_on).strip()
    if normalized:
        if not _ABSOLUTE_DATE_RE.fullmatch(normalized):
            raise ValueError("occurred_on must be an absolute YYYY-MM-DD date")
        return normalized
    inferred = _ABSOLUTE_DATE_RE.search(note)
    return inferred.group(0) if inferred else ""


__all__ = [
    "ALL_LAYERS",
    "ALL_MEMORY_TYPES",
    "SESSION_MEMORY_TYPE",
    "SessionMemoryDecision",
    "default_layer_for_type",
    "normalize_memory_type",
    "typed_memory_entry_payload",
    "validate_layer_for_type",
    "validate_session_memory",
]
