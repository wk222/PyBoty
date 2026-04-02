"""Typed policy helpers for session and durable memory writes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ABSOLUTE_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DERIVABLE_FACT_RE = re.compile(
    r"(`[^`]+`|\.py\b|\.md\b|repo\b|repository\b|codebase\b|function\b|class\b|module\b|workflow file\b)",
    re.IGNORECASE,
)

SESSION_MEMORY_TYPE = "session_note"
_DURABLE_TYPES = {"user", "feedback", "project", "reference"}
_ALLOWED_TYPES = {SESSION_MEMORY_TYPE, *_DURABLE_TYPES}


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

    normalized_type = str(memory_type or SESSION_MEMORY_TYPE).strip().lower() or SESSION_MEMORY_TYPE
    if normalized_type not in _ALLOWED_TYPES:
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
    layer: str,
    source: str = "session_runtime",
) -> dict[str, object]:
    return {
        "memory_type": decision.memory_type,
        "durable": decision.durable,
        "note": decision.note,
        "occurred_on": decision.occurred_on,
        "verified": decision.verified,
        "warnings": list(decision.warnings),
        "layer": str(layer).strip() or "session",
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
