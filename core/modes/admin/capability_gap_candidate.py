"""Data class describing a capability-gap candidate detected by the admin runtime.

Extracted from ``core/modes/admin/runtime.py`` to keep the runtime module focused
on lifecycle and orchestration logic. The candidate is the immutable record that
flows through the admin gap pipeline: detected -> drafted -> validated ->
published -> rolled-out -> promoted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapabilityGapCandidate:
    candidate_id: str
    source: str
    event_type: str
    gap_type: str
    suggested_capability_name: str
    occurrences: int
    samples: list[Any]
    status: str = "detected"
    promoted_task_id: str = ""
    recommended_asset_kind: str = ""
    recommended_publish_target: str = ""
    draft_contract: dict[str, Any] = field(default_factory=dict)
    recommended_steps: list[str] = field(default_factory=list)
    rollout_recommendations: list[str] = field(default_factory=list)
    provider_matches: list[dict[str, Any]] = field(default_factory=list)
    synthesis_goal: str = ""
    draft_artifact: dict[str, Any] = field(default_factory=dict)
    validation_result: dict[str, Any] = field(default_factory=dict)
    publish_result: dict[str, Any] = field(default_factory=dict)
    rollout_state: dict[str, Any] = field(default_factory=dict)
    rollout_history: list[dict[str, Any]] = field(default_factory=list)
    post_release_observations: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source": self.source,
            "event_type": self.event_type,
            "gap_type": self.gap_type,
            "suggested_capability_name": self.suggested_capability_name,
            "occurrences": self.occurrences,
            "samples": self.samples,
            "status": self.status,
            "promoted_task_id": self.promoted_task_id,
            "recommended_asset_kind": self.recommended_asset_kind,
            "recommended_publish_target": self.recommended_publish_target,
            "draft_contract": dict(self.draft_contract),
            "recommended_steps": list(self.recommended_steps),
            "rollout_recommendations": list(self.rollout_recommendations),
            "provider_matches": [dict(item) for item in self.provider_matches],
            "synthesis_goal": self.synthesis_goal,
            "draft_artifact": dict(self.draft_artifact),
            "validation_result": dict(self.validation_result),
            "publish_result": dict(self.publish_result),
            "rollout_state": dict(self.rollout_state),
            "rollout_history": [dict(item) for item in self.rollout_history],
            "post_release_observations": [dict(item) for item in self.post_release_observations],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_event_payload(cls, payload: dict[str, Any]) -> CapabilityGapCandidate:
        source = str(payload.get("source", "unknown"))
        gap_type = str(payload.get("gap_type", "general_runtime_gap"))
        capability_name = str(payload.get("suggested_capability_name", f"{source}_{gap_type}"))
        candidate_key = f"{source}:{gap_type}:{capability_name}".lower()
        candidate_id = candidate_key.replace("/", "_").replace(" ", "_").replace(":", "_")
        return cls(
            candidate_id=candidate_id,
            source=source,
            event_type=str(payload.get("event_type", "")),
            gap_type=gap_type,
            suggested_capability_name=capability_name,
            occurrences=int(payload.get("occurrences", 1)),
            samples=list(payload.get("samples", [])),
            recommended_asset_kind=str(payload.get("recommended_asset_kind", "")),
            recommended_publish_target=str(payload.get("recommended_publish_target", "")),
            draft_contract=dict(payload.get("draft_contract", {})),
            recommended_steps=list(payload.get("recommended_steps", [])),
            rollout_recommendations=list(payload.get("rollout_recommendations", [])),
            provider_matches=list(payload.get("provider_matches", [])),
            synthesis_goal=str(payload.get("synthesis_goal", "")),
            draft_artifact=dict(payload.get("draft_artifact", {})),
            validation_result=dict(payload.get("validation_result", {})),
            publish_result=dict(payload.get("publish_result", {})),
            rollout_state=dict(payload.get("rollout_state", {})),
            rollout_history=list(payload.get("rollout_history", [])),
            post_release_observations=list(payload.get("post_release_observations", [])),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityGapCandidate:
        return cls(
            candidate_id=str(data.get("candidate_id", "")),
            source=str(data.get("source", "")),
            event_type=str(data.get("event_type", "")),
            gap_type=str(data.get("gap_type", "")),
            suggested_capability_name=str(data.get("suggested_capability_name", "")),
            occurrences=int(data.get("occurrences", 1)),
            samples=list(data.get("samples", [])),
            status=str(data.get("status", "detected")),
            promoted_task_id=str(data.get("promoted_task_id", "")),
            recommended_asset_kind=str(data.get("recommended_asset_kind", "")),
            recommended_publish_target=str(data.get("recommended_publish_target", "")),
            draft_contract=dict(data.get("draft_contract", {})),
            recommended_steps=list(data.get("recommended_steps", [])),
            rollout_recommendations=list(data.get("rollout_recommendations", [])),
            provider_matches=list(data.get("provider_matches", [])),
            synthesis_goal=str(data.get("synthesis_goal", "")),
            draft_artifact=dict(data.get("draft_artifact", {})),
            validation_result=dict(data.get("validation_result", {})),
            publish_result=dict(data.get("publish_result", {})),
            rollout_state=dict(data.get("rollout_state", {})),
            rollout_history=list(data.get("rollout_history", [])),
            post_release_observations=list(data.get("post_release_observations", [])),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )
