from __future__ import annotations

import time

import pytest

from core.assets.workflows.node_operator import NodeIdentity, NodeLeaseManager, NodeOperator
from core.assets.workflows.workflow_models import FlowNode, NodeExceptionConfig, NodeStatus, NodeType, WorkflowDef


def test_node_identity_fqdn():
    identity = NodeIdentity(workflow_id="wf1", run_id="r1", node_id="n1", attempt=2)
    assert identity.fqdn == "wf1::r1::n1::a2"


def test_lease_manager_acquires_and_releases():
    manager = NodeLeaseManager()
    identity = NodeIdentity(workflow_id="wf1", run_id="r1", node_id="n1")

    lease1 = manager.acquire(identity, ttl_seconds=10)
    assert lease1 is not None

    lease2 = manager.acquire(identity, ttl_seconds=10)
    assert lease2 is None  # Already leased

    released = manager.release(identity, lease1.lease_id)
    assert released is True

    lease3 = manager.acquire(identity, ttl_seconds=10)
    assert lease3 is not None  # Can lease again after release


def test_node_operator_successful_execution():
    operator = NodeOperator()
    node = FlowNode(id="n1", type=NodeType.EXEC)
    workflow = WorkflowDef(id="wf1", name="test")

    def dispatch(n, w):
        return {"result": "success"}

    events = []

    def log_event(w, n_id, evt, detail):
        events.append((evt, detail))

    result = operator.invoke(node, workflow, "run1", dispatch, log_event)

    assert result == {"result": "success"}
    assert node.status == NodeStatus.COMPLETED
    assert ("start", "type=exec") in events
    assert any(e[0] == "completed" for e in events)


def test_node_operator_timeout():
    operator = NodeOperator()
    node = FlowNode(
        id="n1",
        type=NodeType.EXEC,
        exception_config=NodeExceptionConfig(timeout_seconds=0.1),
    )
    workflow = WorkflowDef(id="wf1", name="test")

    def dispatch(n, w):
        time.sleep(0.5)
        return {"result": "success"}

    with pytest.raises(TimeoutError, match="timed out after 0.1s"):
        operator.invoke(node, workflow, "run1", dispatch, lambda *args: None)

    assert node.status == NodeStatus.FAILED


def test_node_operator_retry():
    operator = NodeOperator()
    node = FlowNode(
        id="n1",
        type=NodeType.EXEC,
        exception_config=NodeExceptionConfig(max_retries=2, retry_delay=0.1),
    )
    workflow = WorkflowDef(id="wf1", name="test")

    attempts = 0

    def dispatch(n, w):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("Temporary error")
        return {"result": "success"}

    events = []

    def log_event(w, n_id, evt, detail):
        events.append(evt)

    result = operator.invoke(node, workflow, "run1", dispatch, log_event)

    assert result == {"result": "success"}
    assert attempts == 3
    assert events.count("retry") == 2
    assert node.status == NodeStatus.COMPLETED


def test_node_operator_fallback():
    operator = NodeOperator()
    node = FlowNode(
        id="n1",
        type=NodeType.EXEC,
        exception_config=NodeExceptionConfig(fallback_output={"fallback": True}),
    )
    workflow = WorkflowDef(id="wf1", name="test")

    def dispatch(n, w):
        raise ValueError("Fatal error")

    events = []

    def log_event(w, n_id, evt, detail):
        events.append(evt)

    result = operator.invoke(node, workflow, "run1", dispatch, log_event)

    assert result == {"fallback": True}
    assert node.status == NodeStatus.COMPLETED
    assert "fallback" in events


def test_node_operator_idempotency():
    operator = NodeOperator()
    node = FlowNode(
        id="n1",
        type=NodeType.EXEC,
        idempotency_key="my_key",
    )
    workflow = WorkflowDef(id="wf1", name="test", variables={"_idempotent:my_key": {"cached": True}})

    attempts = 0

    def dispatch(n, w):
        nonlocal attempts
        attempts += 1
        return {"result": "success"}

    events = []

    def log_event(w, n_id, evt, detail):
        events.append(evt)

    result = operator.invoke(node, workflow, "run1", dispatch, log_event)

    assert result == {"cached": True}
    assert attempts == 0  # Dispatch not called
    assert "idempotent_hit" in events
    assert node.status == NodeStatus.COMPLETED
