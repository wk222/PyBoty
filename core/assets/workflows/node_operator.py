"""Node Operator Control Plane.

Provides a robust execution wrapper for workflow nodes with:
- Node Identity (FQDN across workflow runs)
- Lease mechanism (prevent concurrent execution of the same node in distributed setups)
- Timeout enforcement
- Retry policies with exponential backoff
- Fallback handling
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .workflow_exceptions import WorkflowApprovalPause, WorkflowSignalPause, WorkflowTimerPause
from .workflow_models import FlowNode, NodeStatus, WorkflowDef

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeIdentity:
    """Fully qualified identity for a node execution."""

    workflow_id: str
    run_id: str
    node_id: str
    attempt: int = 1

    @property
    def fqdn(self) -> str:
        return f"{self.workflow_id}::{self.run_id}::{self.node_id}::a{self.attempt}"


@dataclass
class NodeLease:
    """Lease for distributed node execution."""

    lease_id: str
    worker_id: str
    acquired_at: float
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class NodeLeaseManager:
    """In-memory lease manager (can be backed by Redis/DB in production)."""

    def __init__(self) -> None:
        self._leases: dict[str, NodeLease] = {}

    def acquire(self, identity: NodeIdentity, ttl_seconds: float = 300) -> NodeLease | None:
        key = f"{identity.workflow_id}::{identity.run_id}::{identity.node_id}"
        now = time.time()
        existing = self._leases.get(key)
        if existing and not existing.is_expired:
            return None  # Already leased

        lease = NodeLease(
            lease_id=str(uuid.uuid4()),
            worker_id=str(uuid.uuid4()),  # In a real distributed system, this would be the pod/worker ID
            acquired_at=now,
            expires_at=now + ttl_seconds,
        )
        self._leases[key] = lease
        return lease

    def release(self, identity: NodeIdentity, lease_id: str) -> bool:
        key = f"{identity.workflow_id}::{identity.run_id}::{identity.node_id}"
        existing = self._leases.get(key)
        if existing and existing.lease_id == lease_id:
            del self._leases[key]
            return True
        return False

    def prune_expired(self) -> int:
        """Remove expired leases. Returns the number of pruned leases."""
        now = time.time()
        expired_keys = [k for k, v in self._leases.items() if now > v.expires_at]
        for k in expired_keys:
            del self._leases[k]
        return len(expired_keys)


class NodeOperator:
    """Strong control plane for node execution."""

    def __init__(self, lease_manager: NodeLeaseManager | None = None):
        self.lease_manager = lease_manager or NodeLeaseManager()

    def invoke(
        self,
        node: FlowNode,
        workflow: WorkflowDef,
        run_id: str,
        dispatch_fn: Callable[[FlowNode, WorkflowDef], Any],
        log_event: Callable[[WorkflowDef, str, str, str], None],
    ) -> Any:
        """Invoke a node with lease, timeout, retry, and fallback mechanisms."""
        idem_key = node.idempotency_key or node.config.get("idempotency_key")
        if idem_key:
            cache_key = f"_idempotent:{idem_key}"
            cached = workflow.variables.get(cache_key)
            if cached is not None:
                log_event(workflow, node.id, "idempotent_hit", f"key={idem_key}")
                self._mark_node_completed(node, workflow, cached)
                return cached

        node.status = NodeStatus.RUNNING
        node.started_at = time.time()
        log_event(workflow, node.id, "start", f"type={node.type.value}")

        identity = NodeIdentity(
            workflow_id=workflow.id,
            run_id=run_id,
            node_id=node.id,
            attempt=node.retry_count + 1,
        )

        lease_ttl = float(node.timeout_seconds or 300) + 60.0  # Add buffer for lease
        lease = self.lease_manager.acquire(identity, ttl_seconds=lease_ttl)
        if not lease:
            error_msg = f"Failed to acquire lease for node {identity.fqdn} (already running)"
            log_event(workflow, node.id, "lease_failed", error_msg)
            raise RuntimeError(error_msg)

        try:
            result = self._execute_with_retry(node, workflow, identity, dispatch_fn, log_event)
            self._mark_node_completed(node, workflow, result)
            if idem_key:
                workflow.variables[f"_idempotent:{idem_key}"] = result
            log_event(workflow, node.id, "completed", str(result)[:200] if result else "")
            return result
        except (WorkflowApprovalPause, WorkflowTimerPause, WorkflowSignalPause):
            raise
        except Exception as exc:
            if node.fallback_output is not None:
                log_event(
                    workflow,
                    node.id,
                    "fallback",
                    f"Using fallback after {type(exc).__name__}: {exc}",
                )
                node.status = NodeStatus.COMPLETED
                node.output = node.fallback_output
                node.error = f"fallback: {exc}"
                node.completed_at = time.time()
                workflow.variables[f"{node.id}.output"] = node.fallback_output
                workflow.variables[f"{node.id}.status"] = "fallback"
                return node.fallback_output

            node.status = NodeStatus.FAILED
            node.error = str(exc)
            node.completed_at = time.time()
            workflow.variables[f"{node.id}.status"] = "failed"
            workflow.variables[f"{node.id}.error"] = str(exc)
            log_event(workflow, node.id, "failed", str(exc))
            raise
        finally:
            self.lease_manager.release(identity, lease.lease_id)

    def _execute_with_retry(
        self,
        node: FlowNode,
        workflow: WorkflowDef,
        identity: NodeIdentity,
        dispatch_fn: Callable[[FlowNode, WorkflowDef], Any],
        log_event: Callable[[WorkflowDef, str, str, str], None],
    ) -> Any:
        while True:
            try:
                return self._exec_with_timeout(node, workflow, dispatch_fn)
            except (WorkflowApprovalPause, WorkflowTimerPause, WorkflowSignalPause):
                raise
            except Exception as exc:
                max_retries = node.max_retries or int(node.config.get("retries", 0))
                if node.retry_count >= max_retries:
                    raise

                node.retry_count += 1
                base_delay = node.retry_delay or node.config.get("retry_delay", 1.0)
                delay = min(
                    base_delay * (2 ** (node.retry_count - 1)),
                    node.max_retry_delay,
                )
                log_event(
                    workflow,
                    node.id,
                    "retry",
                    f"{node.retry_count}/{max_retries}, delay={delay:.1f}s, error={exc}",
                )
                time.sleep(delay)

    def _exec_with_timeout(
        self,
        node: FlowNode,
        workflow: WorkflowDef,
        dispatch_fn: Callable[[FlowNode, WorkflowDef], Any],
    ) -> Any:
        timeout = node.timeout_seconds or node.config.get("timeout")
        if timeout is None:
            return dispatch_fn(node, workflow)

        timeout_secs = float(timeout)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(dispatch_fn, node, workflow)
            try:
                return future.result(timeout=timeout_secs)
            except concurrent.futures.TimeoutError:
                future.cancel()
                raise TimeoutError(f"Node '{node.id}' timed out after {timeout_secs}s") from None

    def _mark_node_completed(self, node: FlowNode, workflow: WorkflowDef, result: Any) -> None:
        node.status = NodeStatus.COMPLETED
        node.output = result
        node.completed_at = time.time()
        workflow.variables[f"{node.id}.output"] = result
        workflow.variables[f"{node.id}.status"] = "completed"
