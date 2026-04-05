"""Shared execution protocol for approval interrupts and workflow pause metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .approval_queue import ApprovalQueue, ApprovalRequest


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _copy_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


@dataclass(frozen=True)
class PendingApprovalRef:
    """Normalized reference to one pending approval carried by a workflow payload."""

    approval_id: str
    agent_name: str = ""
    task: str = ""
    context: str = ""
    thread_id: str = ""
    response: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, value: Any) -> PendingApprovalRef | None:
        if not isinstance(value, dict):
            return None

        approval_id = _clean_str(value.get("approval_id"))
        if not approval_id:
            return None

        payload = _copy_dict(value.get("payload"))
        extra = dict(value)
        for key in (
            "approval_id",
            "agent_name",
            "task",
            "context",
            "thread_id",
            "response",
            "payload",
        ):
            extra.pop(key, None)

        return cls(
            approval_id=approval_id,
            agent_name=_clean_str(value.get("agent_name")),
            task=_clean_str(value.get("task")),
            context=_clean_str(value.get("context")),
            thread_id=_clean_str(value.get("thread_id")),
            response=_clean_str(value.get("response")),
            payload=payload,
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        normalized = {
            **self.extra,
            "approval_id": self.approval_id,
            "task": self.task,
            "context": self.context,
        }
        if self.agent_name:
            normalized["agent_name"] = self.agent_name
        if self.thread_id:
            normalized["thread_id"] = self.thread_id
        if self.response:
            normalized["response"] = self.response
        if self.payload:
            normalized["payload"] = dict(self.payload)
        return normalized


def normalize_pending_approval_refs(value: Any) -> list[PendingApprovalRef]:
    """Return normalized pending-approval references with stable deduplication."""
    if not isinstance(value, list):
        return []

    normalized: list[PendingApprovalRef] = []
    seen: set[str] = set()
    for item in value:
        pending = PendingApprovalRef.from_payload(item)
        if pending is None or pending.approval_id in seen:
            continue
        normalized.append(pending)
        seen.add(pending.approval_id)
    return normalized


@dataclass(frozen=True)
class WaitingApprovalPayload:
    """Typed view over a waiting-approval payload exchanged across runtimes."""

    approval_id: str = ""
    approval_ids: tuple[str, ...] = ()
    pending_approvals: tuple[PendingApprovalRef, ...] = ()
    status: str = ""
    response: str = ""
    thread_id: str = ""
    workflow_pause_kind: str = ""
    workflow_pause_mode: str = ""
    workflow_pause_state: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> WaitingApprovalPayload:
        payload_dict = _copy_dict(payload)
        pending_approvals = tuple(normalize_pending_approval_refs(payload_dict.get("pending_approvals")))

        approval_ids: list[str] = []
        primary_approval_id = _clean_str(payload_dict.get("approval_id"))
        if primary_approval_id:
            approval_ids.append(primary_approval_id)

        raw_approval_ids = payload_dict.get("approval_ids", [])
        if isinstance(raw_approval_ids, list):
            for item in raw_approval_ids:
                approval_id = _clean_str(item)
                if approval_id and approval_id not in approval_ids:
                    approval_ids.append(approval_id)

        for pending in pending_approvals:
            if pending.approval_id not in approval_ids:
                approval_ids.append(pending.approval_id)

        primary = primary_approval_id or (approval_ids[0] if approval_ids else "")
        return cls(
            approval_id=primary,
            approval_ids=tuple(approval_ids),
            pending_approvals=pending_approvals,
            status=_clean_str(payload_dict.get("status")),
            response=_clean_str(payload_dict.get("response")),
            thread_id=_clean_str(payload_dict.get("thread_id")),
            workflow_pause_kind=_clean_str(payload_dict.get("workflow_pause_kind")),
            workflow_pause_mode=_clean_str(payload_dict.get("workflow_pause_mode")),
            workflow_pause_state=_copy_dict(payload_dict.get("workflow_pause_state")),
        )

    @property
    def primary_approval_id(self) -> str:
        if self.approval_id:
            return self.approval_id
        return self.approval_ids[0] if self.approval_ids else ""

    @property
    def all_approval_ids(self) -> tuple[str, ...]:
        if self.approval_ids:
            return self.approval_ids
        if self.approval_id:
            return (self.approval_id,)
        return ()

    @property
    def is_delegated_subagent_pause(self) -> bool:
        return self.workflow_pause_kind == "delegated_subagent"

    def to_payload(self, base_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        updated = _copy_dict(base_payload)
        approval_id = self.primary_approval_id
        approval_ids = list(self.all_approval_ids)
        had_explicit_approval_ids = isinstance(base_payload, dict) and "approval_ids" in base_payload

        if approval_id:
            updated["approval_id"] = approval_id
        else:
            updated.pop("approval_id", None)

        if approval_ids and (len(approval_ids) > 1 or self.pending_approvals or had_explicit_approval_ids):
            updated["approval_ids"] = approval_ids
        else:
            updated.pop("approval_ids", None)

        if self.pending_approvals:
            updated["pending_approvals"] = [item.to_dict() for item in self.pending_approvals]
        else:
            updated.pop("pending_approvals", None)

        return updated


@dataclass(frozen=True)
class WorkflowPauseMetadata:
    """Workflow pause metadata attached to approval requests and resume responses."""

    workflow_id: str = ""
    workflow_name: str = ""
    workflow_node_id: str = ""
    workflow_node_label: str = ""
    workflow_resume_token: str = ""
    workflow_pause_kind: str = ""
    workflow_pause_mode: str = ""

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None) -> WorkflowPauseMetadata:
        metadata_dict = _copy_dict(metadata)
        return cls(
            workflow_id=_clean_str(metadata_dict.get("workflow_id")),
            workflow_name=_clean_str(metadata_dict.get("workflow_name")),
            workflow_node_id=(
                _clean_str(metadata_dict.get("workflow_node_id"))
                or _clean_str(metadata_dict.get("node_id"))
            ),
            workflow_node_label=(
                _clean_str(metadata_dict.get("workflow_node_label"))
                or _clean_str(metadata_dict.get("node_label"))
            ),
            workflow_resume_token=(
                _clean_str(metadata_dict.get("workflow_resume_token"))
                or _clean_str(metadata_dict.get("resume_token"))
            ),
            workflow_pause_kind=_clean_str(metadata_dict.get("workflow_pause_kind")),
            workflow_pause_mode=_clean_str(metadata_dict.get("workflow_pause_mode")),
        )

    @classmethod
    def from_waiting_payload(
        cls,
        *,
        workflow_id: str,
        workflow_name: str,
        node_id: str,
        node_label: str,
        resume_token: str,
        payload: dict[str, Any] | None,
    ) -> WorkflowPauseMetadata:
        waiting = WaitingApprovalPayload.from_payload(payload)
        return cls(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            workflow_node_id=node_id,
            workflow_node_label=node_label,
            workflow_resume_token=resume_token,
            workflow_pause_kind=waiting.workflow_pause_kind or "delegated_subagent",
            workflow_pause_mode=waiting.workflow_pause_mode,
        )

    @property
    def should_resume_delegated_subagent(self) -> bool:
        return (
            self.workflow_pause_kind == "delegated_subagent"
            and bool(self.workflow_id)
            and bool(self.workflow_resume_token)
        )

    def to_request_metadata_updates(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "workflow_node_id": self.workflow_node_id,
            "workflow_node_label": self.workflow_node_label,
            "workflow_resume_token": self.workflow_resume_token,
            "workflow_pause_kind": self.workflow_pause_kind,
            "workflow_pause_mode": self.workflow_pause_mode,
        }

    def approval_response(self, *, prompt: str, approval_id: str) -> dict[str, Any]:
        return build_workflow_approval_response(
            workflow_id=self.workflow_id,
            node_id=self.workflow_node_id,
            resume_token=self.workflow_resume_token,
            prompt=prompt,
            approval_id=approval_id,
        )


@dataclass(frozen=True)
class ApprovalResolutionContext:
    """Routing context derived from an approval request's execution metadata."""

    request_kind: str = ""
    parent_thread_id: str = ""
    target: str = ""
    thread_id: str = ""
    workflow_pause: WorkflowPauseMetadata = field(default_factory=WorkflowPauseMetadata)

    @classmethod
    def from_request(cls, request: ApprovalRequest | None) -> ApprovalResolutionContext:
        metadata = request.metadata if request is not None else {}
        return cls(
            request_kind=_clean_str(getattr(request, "kind", "")),
            parent_thread_id=_clean_str(metadata.get("parent_thread_id")),
            target=_clean_str(metadata.get("target")),
            thread_id=_clean_str(metadata.get("thread_id")),
            workflow_pause=WorkflowPauseMetadata.from_metadata(metadata),
        )

    @property
    def routes_to_parent_thread(self) -> bool:
        return bool(self.parent_thread_id)

    @property
    def routes_to_root_thread(self) -> bool:
        return self.request_kind == "tool_call" and self.target == "root_agent" and bool(self.thread_id)

    @property
    def routes_to_system_agent(self) -> bool:
        return self.request_kind == "tool_call" and self.target.startswith("subagent:")

    def resolve_agent(
        self,
        *,
        get_agent_for_thread,
        get_system_agent,
    ) -> Any | None:
        if self.routes_to_parent_thread:
            return get_agent_for_thread(self.parent_thread_id)
        if self.routes_to_root_thread:
            return get_agent_for_thread(self.thread_id)
        if self.routes_to_system_agent:
            return get_system_agent()
        return None


def attach_workflow_pause_metadata(
    *,
    approval_queue: ApprovalQueue,
    approval_ids: list[str] | tuple[str, ...],
    primary_approval_id: str,
    pause_metadata: WorkflowPauseMetadata,
    default_prompt: str,
) -> str:
    """Attach the same workflow pause metadata to each pending approval request."""
    prompt = default_prompt
    ids = [approval_id for approval_id in approval_ids if _clean_str(approval_id)]
    if not ids and primary_approval_id:
        ids = [primary_approval_id]

    seen: set[str] = set()
    updates = pause_metadata.to_request_metadata_updates()
    for approval_id in ids:
        if approval_id in seen:
            continue
        seen.add(approval_id)
        request = approval_queue.get_request(approval_id)
        if request is None:
            continue
        approval_queue.update_request_metadata(approval_id, **updates)
        if approval_id == primary_approval_id:
            prompt = request.prompt or prompt

    return prompt


def build_workflow_approval_response(
    *,
    workflow_id: str,
    node_id: str,
    resume_token: str,
    prompt: str,
    approval_id: str,
) -> dict[str, Any]:
    return {
        "status": "waiting_approval",
        "workflow_id": workflow_id,
        "node_id": node_id,
        "resume_token": resume_token,
        "prompt": prompt,
        "approval_id": approval_id,
    }
