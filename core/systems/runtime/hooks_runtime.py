"""Structured hooks runtime with fixed lifecycle phases and controlled outputs."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class HookPhase(str, Enum):
    INSTRUCTION_ASSEMBLY = "instruction_assembly"
    SESSION_BOOKKEEPING = "session_bookkeeping"
    PERMISSION_DECISION = "permission_decision"
    ROUTE_SELECTION = "route_selection"
    SUBAGENT_ISOLATION = "subagent_isolation"
    CONTEXT_HYGIENE_COMPACT_DECISION = "context_hygiene_compact_decision"
    CONTEXT_HYGIENE_REBUILD = "context_hygiene_rebuild"
    CONTEXT_HYGIENE_WRITEBACK = "context_hygiene_writeback"
    CONTEXT_HYGIENE_OFFLOAD = "context_hygiene_offload"
    CONTEXT_HYGIENE_SNIP_FAILURE = "context_hygiene_snip_failure"


_ALLOWED_OUTPUTS: dict[HookPhase, frozenset[str]] = {
    HookPhase.INSTRUCTION_ASSEMBLY: frozenset({"prepend_sections", "append_sections", "notes", "session_tags"}),
    HookPhase.SESSION_BOOKKEEPING: frozenset({"notes", "session_tags"}),
    HookPhase.PERMISSION_DECISION: frozenset({"verdict", "reason_fragments", "tags"}),
    HookPhase.ROUTE_SELECTION: frozenset(
        {"prefer_slots", "avoid_slots", "avoid_top_levels", "force_trunk_first", "notes"}
    ),
    HookPhase.SUBAGENT_ISOLATION: frozenset(
        {"labels", "notes", "requires_strict_isolation", "requires_workspace_visibility"}
    ),
    HookPhase.CONTEXT_HYGIENE_COMPACT_DECISION: frozenset({"force_compact", "force_skip", "notes"}),
    HookPhase.CONTEXT_HYGIENE_REBUILD: frozenset({"prepend_sections", "append_sections", "notes"}),
    HookPhase.CONTEXT_HYGIENE_WRITEBACK: frozenset({"notes", "session_tags", "boundary_annotations"}),
    HookPhase.CONTEXT_HYGIENE_OFFLOAD: frozenset({"offload_strategy", "notes", "abort_offload"}),
    HookPhase.CONTEXT_HYGIENE_SNIP_FAILURE: frozenset({"recovery_strategy", "notes", "fallback_boundary"}),
}

_VERDICT_PRIORITY = {"allow": 0, "ask": 1, "deny": 2}
_MAX_RECENT_RUNS = 24


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _append_unique(items: list[str], value: Any) -> None:
    normalized = _as_text(value)
    if normalized and normalized not in items:
        items.append(normalized)


def _merge_bool(current: bool, incoming: Any) -> bool:
    return bool(current) or bool(incoming)


@dataclass(slots=True)
class RuntimeHook:
    phase: HookPhase
    name: str
    handler: Callable[[dict[str, Any]], dict[str, Any] | None]
    priority: int = 0


class HooksRuntime:
    """Single owner for fixed-phase runtime hooks.

    Hooks do not get arbitrary write access to the runtime. Each phase has a
    very small allow-list of writable fields, and merges are monotonic:
    permission hooks can only tighten a verdict, route hooks can only add
    constraints, and prompt hooks can only append/prepend sections.
    """

    def __init__(self) -> None:
        self._hooks: dict[HookPhase, list[RuntimeHook]] = {phase: [] for phase in HookPhase}
        self._recent_runs: list[dict[str, Any]] = []

    def register(
        self,
        phase: HookPhase | str,
        name: str,
        handler: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        priority: int = 0,
    ) -> None:
        resolved_phase = phase if isinstance(phase, HookPhase) else HookPhase(str(phase).strip().lower())
        hook = RuntimeHook(
            phase=resolved_phase,
            name=_as_text(name) or getattr(handler, "__name__", "hook"),
            handler=handler,
            priority=int(priority or 0),
        )
        hooks = self._hooks.setdefault(resolved_phase, [])
        hooks.append(hook)
        hooks.sort(key=lambda item: (-item.priority, item.name))

    def run_phase(self, phase: HookPhase | str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        resolved_phase = phase if isinstance(phase, HookPhase) else HookPhase(str(phase).strip().lower())
        current = self._empty_result_for_phase(resolved_phase)
        current["phase"] = resolved_phase.value
        current["handler_names"] = []
        safe_payload = dict(payload or {})

        for hook in self._hooks.get(resolved_phase, []):
            try:
                raw_result = hook.handler(dict(safe_payload))
            except Exception as exc:
                self._record_run(
                    phase=resolved_phase,
                    hook_name=hook.name,
                    output={},
                    error=str(exc),
                )
                continue
            filtered = self._filter_outputs(resolved_phase, raw_result)
            current = self._merge_phase_result(resolved_phase, current, filtered)
            current["handler_names"].append(hook.name)
            self._record_run(phase=resolved_phase, hook_name=hook.name, output=filtered)
        return current

    def build_projection(self, *, limit: int = 8) -> dict[str, Any]:
        capped_limit = max(1, int(limit or 0))
        phase_counts = [
            {
                "phase": phase.value,
                "handler_count": len(self._hooks.get(phase, [])),
            }
            for phase in HookPhase
            if self._hooks.get(phase)
        ]
        summary = (
            f"{sum(item['handler_count'] for item in phase_counts)} hooks across "
            f"{len(phase_counts)} active phases"
        )
        return {
            "summary": summary,
            "active_phases": [item["phase"] for item in phase_counts],
            "phase_counts": phase_counts,
            "recent_runs": list(self._recent_runs[-capped_limit:]),
        }

    def _empty_result_for_phase(self, phase: HookPhase) -> dict[str, Any]:
        if phase == HookPhase.INSTRUCTION_ASSEMBLY:
            return {"prepend_sections": [], "append_sections": [], "notes": [], "session_tags": []}
        if phase == HookPhase.SESSION_BOOKKEEPING:
            return {"notes": [], "session_tags": []}
        if phase == HookPhase.PERMISSION_DECISION:
            return {"verdict": "", "reason_fragments": [], "tags": []}
        if phase == HookPhase.ROUTE_SELECTION:
            return {
                "prefer_slots": [],
                "avoid_slots": [],
                "avoid_top_levels": [],
                "force_trunk_first": False,
                "notes": [],
            }
        if phase == HookPhase.CONTEXT_HYGIENE_COMPACT_DECISION:
            return {"force_compact": False, "force_skip": False, "notes": []}
        if phase == HookPhase.CONTEXT_HYGIENE_REBUILD:
            return {"prepend_sections": [], "append_sections": [], "notes": []}
        if phase == HookPhase.CONTEXT_HYGIENE_WRITEBACK:
            return {"notes": [], "session_tags": [], "boundary_annotations": {}}
        return {
            "labels": [],
            "notes": [],
            "requires_strict_isolation": False,
            "requires_workspace_visibility": False,
        }

    def _filter_outputs(self, phase: HookPhase, payload: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        allowed = _ALLOWED_OUTPUTS[phase]
        return {key: value for key, value in payload.items() if key in allowed}

    def _merge_phase_result(
        self,
        phase: HookPhase,
        current: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current)
        if phase in {HookPhase.INSTRUCTION_ASSEMBLY, HookPhase.SESSION_BOOKKEEPING}:
            for key in ("prepend_sections", "append_sections", "notes", "session_tags"):
                if key not in incoming:
                    continue
                target = list(merged.get(key, []))
                for value in incoming.get(key, []) if isinstance(incoming.get(key), list) else [incoming.get(key)]:
                    _append_unique(target, value)
                merged[key] = target
            return merged

        if phase == HookPhase.CONTEXT_HYGIENE_COMPACT_DECISION:
            for key in ("notes",):
                target = list(merged.get(key, []))
                for value in incoming.get(key, []) if isinstance(incoming.get(key), list) else [incoming.get(key)]:
                    _append_unique(target, value)
                merged[key] = target
            merged["force_compact"] = _merge_bool(bool(merged.get("force_compact")), incoming.get("force_compact"))
            merged["force_skip"] = _merge_bool(bool(merged.get("force_skip")), incoming.get("force_skip"))
            return merged

        if phase == HookPhase.CONTEXT_HYGIENE_REBUILD:
            for key in ("prepend_sections", "append_sections", "notes"):
                target = list(merged.get(key, []))
                for value in incoming.get(key, []) if isinstance(incoming.get(key), list) else [incoming.get(key)]:
                    _append_unique(target, value)
                merged[key] = target
            return merged

        if phase == HookPhase.CONTEXT_HYGIENE_WRITEBACK:
            for key in ("notes", "session_tags"):
                target = list(merged.get(key, []))
                for value in incoming.get(key, []) if isinstance(incoming.get(key), list) else [incoming.get(key)]:
                    _append_unique(target, value)
                merged[key] = target
            annotations = dict(merged.get("boundary_annotations", {}))
            if isinstance(incoming.get("boundary_annotations"), dict):
                annotations.update(incoming.get("boundary_annotations", {}))
            merged["boundary_annotations"] = annotations
            return merged

        if phase == HookPhase.PERMISSION_DECISION:
            incoming_verdict = _as_text(incoming.get("verdict")).lower()
            current_verdict = _as_text(merged.get("verdict")).lower()
            if incoming_verdict and _VERDICT_PRIORITY.get(incoming_verdict, -1) > _VERDICT_PRIORITY.get(
                current_verdict, -1
            ):
                merged["verdict"] = incoming_verdict
            for key in ("reason_fragments", "tags"):
                target = list(merged.get(key, []))
                for value in incoming.get(key, []) if isinstance(incoming.get(key), list) else [incoming.get(key)]:
                    _append_unique(target, value)
                merged[key] = target
            return merged

        if phase == HookPhase.ROUTE_SELECTION:
            for key in ("prefer_slots", "avoid_slots", "avoid_top_levels", "notes"):
                target = list(merged.get(key, []))
                for value in incoming.get(key, []) if isinstance(incoming.get(key), list) else [incoming.get(key)]:
                    _append_unique(target, value)
                merged[key] = target
            merged["force_trunk_first"] = _merge_bool(
                bool(merged.get("force_trunk_first")),
                incoming.get("force_trunk_first"),
            )
            return merged

        for key in ("labels", "notes"):
            target = list(merged.get(key, []))
            for value in incoming.get(key, []) if isinstance(incoming.get(key), list) else [incoming.get(key)]:
                _append_unique(target, value)
            merged[key] = target
        merged["requires_strict_isolation"] = _merge_bool(
            bool(merged.get("requires_strict_isolation")),
            incoming.get("requires_strict_isolation"),
        )
        merged["requires_workspace_visibility"] = _merge_bool(
            bool(merged.get("requires_workspace_visibility")),
            incoming.get("requires_workspace_visibility"),
        )
        return merged

    def _record_run(
        self,
        *,
        phase: HookPhase,
        hook_name: str,
        output: dict[str, Any],
        error: str = "",
    ) -> None:
        self._recent_runs.append(
            {
                "phase": phase.value,
                "hook_name": hook_name,
                "keys": sorted(output.keys()),
                "timestamp": time.time(),
                "error": error,
            }
        )
        self._recent_runs = self._recent_runs[-_MAX_RECENT_RUNS:]


def _view_section(payload: dict[str, Any], key: str) -> dict[str, Any]:
    view = payload.get("projected_runtime_view")
    if isinstance(view, dict) and isinstance(view.get(key), dict):
        return dict(view.get(key, {}))
    return {}


def _permission_mode_from_payload(payload: dict[str, Any]) -> str:
    permission = _view_section(payload, "permission")
    settings = _view_section(payload, "settings")
    return _as_text(permission.get("mode")) or _as_text(settings.get("permission_mode")) or "default"


def _context_hygiene_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _view_section(payload, "context_hygiene")


def _default_instruction_guard(payload: dict[str, Any]) -> dict[str, Any]:
    mode = _permission_mode_from_payload(payload)
    hygiene = _context_hygiene_from_payload(payload)
    append_sections: list[str] = []
    if mode == "plan":
        append_sections.append(
            "### Runtime Guardrail\nPermission mode is `plan`, so keep mutations disabled and prefer analysis-first routing."
        )
    if bool(hygiene.get("summary_active")):
        append_sections.append(
            "### Runtime Guardrail\nContext has been compacted; prefer the resume bundle and trunk capabilities before opening new branches."
        )
    return {"append_sections": append_sections}


def _default_route_guard(payload: dict[str, Any]) -> dict[str, Any]:
    mode = _permission_mode_from_payload(payload)
    hygiene = _context_hygiene_from_payload(payload)
    result: dict[str, Any] = {
        "prefer_slots": [],
        "avoid_slots": [],
        "avoid_top_levels": [],
        "force_trunk_first": False,
        "notes": [],
    }
    if mode == "plan":
        result["prefer_slots"] = ["tool_runtime_governance", "workspace_view", "session_continuity"]
        result["avoid_slots"] = ["app_orchestration", "workflow_collaboration", "subagent_runtime"]
        result["force_trunk_first"] = True
        result["notes"] = ["plan mode biases routing toward analysis and trunk capabilities"]
    if bool(hygiene.get("summary_active")):
        result["prefer_slots"] = list(result.get("prefer_slots", [])) + ["session_continuity", "context_hygiene"]
        result["notes"] = list(result.get("notes", [])) + ["compacted sessions should resume from the projected view"]
    return result


def _default_permission_guard(payload: dict[str, Any]) -> dict[str, Any]:
    mode = _permission_mode_from_payload(payload)
    tool_name = _as_text(payload.get("tool_name"))
    if mode != "plan":
        return {}
    if tool_name in {"write_file", "str_replace", "bash", "create_app", "update_app_file"}:
        return {
            "verdict": "deny",
            "reason_fragments": ["plan mode blocks mutations until the user switches permission mode"],
            "tags": ["plan_mode_guard"],
        }
    return {
        "reason_fragments": ["plan mode is active; keep this action read-only"],
        "tags": ["plan_mode_guard"],
    }


def _default_session_bookkeeping(payload: dict[str, Any]) -> dict[str, Any]:
    mode = _permission_mode_from_payload(payload)
    tags: list[str] = []
    if mode:
        tags.append(f"permission:{mode}")
    route = _view_section(payload, "route")
    top_level = _as_text(route.get("recommended", {}).get("top_level")) if isinstance(route.get("recommended"), dict) else ""
    if top_level:
        tags.append(f"route:{top_level}")
    return {"session_tags": tags}


def _default_subagent_isolation_guard(payload: dict[str, Any]) -> dict[str, Any]:
    sandbox = payload.get("sandbox", {}) if isinstance(payload.get("sandbox"), dict) else {}
    labels: list[str] = []
    visibility = _as_text(sandbox.get("visibility"))
    if visibility:
        labels.append(f"visibility:{visibility}")
    if not bool(sandbox.get("allows_writes")):
        labels.append("read_only")
    return {
        "labels": labels,
        "requires_strict_isolation": visibility == "isolated",
        "requires_workspace_visibility": visibility == "project",
    }


def create_default_hooks_runtime() -> HooksRuntime:
    runtime = HooksRuntime()
    runtime.register(HookPhase.INSTRUCTION_ASSEMBLY, "default_instruction_guard", _default_instruction_guard, priority=20)
    runtime.register(HookPhase.ROUTE_SELECTION, "default_route_guard", _default_route_guard, priority=20)
    runtime.register(HookPhase.PERMISSION_DECISION, "default_permission_guard", _default_permission_guard, priority=20)
    runtime.register(
        HookPhase.SESSION_BOOKKEEPING,
        "default_session_bookkeeping",
        _default_session_bookkeeping,
        priority=10,
    )
    runtime.register(
        HookPhase.SUBAGENT_ISOLATION,
        "default_subagent_isolation_guard",
        _default_subagent_isolation_guard,
        priority=10,
    )
    return runtime


__all__ = [
    "HookPhase",
    "HooksRuntime",
    "RuntimeHook",
    "create_default_hooks_runtime",
]
