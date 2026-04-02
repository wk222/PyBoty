"""Tests for Temporal-inspired workflow features:

- Durable timer (DELAY with persist + resume)
- Signal/event wait node (wait_signal)
- Idempotency key for nodes
- WorkflowTimerPause / WorkflowSignalPause exceptions
- send_signal on PyFlowEngine
"""

from __future__ import annotations

import time

import pytest

from core.assets.workflows.workflow_models import FlowNode, NodeStatus, NodeType, WorkflowDef
from core.assets.workflows.workflow_node_runtime import (
    WorkflowSignalPause,
    WorkflowTimerPause,
)

# ── Exception classes ─────────────────────────────────────────────


class TestPauseExceptions:
    def test_timer_pause_attrs(self):
        exc = WorkflowTimerPause("wf1", "n1", 9999.0, "tok123")
        assert exc.workflow_id == "wf1"
        assert exc.node_id == "n1"
        assert exc.resume_at == 9999.0
        assert exc.resume_token == "tok123"

    def test_signal_pause_attrs(self):
        exc = WorkflowSignalPause("wf2", "n2", "order_completed", "tok456")
        assert exc.workflow_id == "wf2"
        assert exc.signal_name == "order_completed"
        assert exc.resume_token == "tok456"


# ── Idempotency key ──────────────────────────────────────────────


class TestIdempotencyKey:
    def test_idempotency_key_on_node(self):
        node = FlowNode(id="n1", type=NodeType.EXEC, idempotency_key="unique_123")
        assert node.idempotency_key == "unique_123"

    def test_idempotency_default_none(self):
        node = FlowNode(id="n2", type=NodeType.EXEC)
        assert node.idempotency_key is None


# ── WAIT_SIGNAL node type ────────────────────────────────────────


class TestWaitSignalNodeType:
    def test_enum_exists(self):
        assert NodeType.WAIT_SIGNAL.value == "wait_signal"


# ── Durable DELAY behavior ──────────────────────────────────────


class TestDurableDelay:
    def _make_runtime(self):
        from unittest.mock import MagicMock

        from core.assets.workflows.workflow_node_runtime import WorkflowNodeRuntime

        return WorkflowNodeRuntime(
            workspace_dir="/tmp/test",
            approval_queue=MagicMock(),
            save_workflow=MagicMock(),
            load_workflow=MagicMock(),
            resume_workflow=MagicMock(),
            run_workflow=MagicMock(),
            resolve_var=lambda v, w: v,
            resolve_config=lambda c, w: c,
            evaluate_condition=lambda c, w: True,
            get_predecessors=lambda w, n: [],
            workflow_approval_fingerprint=lambda **kw: "fp",
            log_event=lambda *a: None,
            extra_dispatch=lambda *a: None,
        )

    def test_short_delay_runs_inline(self):
        runtime = self._make_runtime()
        node = FlowNode(id="d1", type=NodeType.DELAY, config={"seconds": 0.01})
        wf = WorkflowDef(id="w1", name="test")
        result = runtime.exec_node(node, wf)
        assert result["durable"] is False
        assert node.status == NodeStatus.COMPLETED

    def test_long_delay_raises_timer_pause(self):
        runtime = self._make_runtime()
        node = FlowNode(id="d2", type=NodeType.DELAY, config={"seconds": 3600, "durable": True})
        wf = WorkflowDef(id="w2", name="test")
        with pytest.raises(WorkflowTimerPause) as exc_info:
            runtime.exec_node(node, wf)
        assert exc_info.value.resume_at > time.time()

    def test_explicit_durable_flag(self):
        runtime = self._make_runtime()
        node = FlowNode(id="d3", type=NodeType.DELAY, config={"seconds": 10, "durable": True})
        wf = WorkflowDef(id="w3", name="test")
        with pytest.raises(WorkflowTimerPause):
            runtime.exec_node(node, wf)


# ── wait_signal behavior ─────────────────────────────────────────


class TestWaitSignal:
    def _make_runtime(self):
        from unittest.mock import MagicMock

        from core.assets.workflows.workflow_node_runtime import WorkflowNodeRuntime

        return WorkflowNodeRuntime(
            workspace_dir="/tmp/test",
            approval_queue=MagicMock(),
            save_workflow=MagicMock(),
            load_workflow=MagicMock(),
            resume_workflow=MagicMock(),
            run_workflow=MagicMock(),
            resolve_var=lambda v, w: v,
            resolve_config=lambda c, w: c,
            evaluate_condition=lambda c, w: True,
            get_predecessors=lambda w, n: [],
            workflow_approval_fingerprint=lambda **kw: "fp",
            log_event=lambda *a: None,
            extra_dispatch=lambda *a: None,
        )

    def test_wait_signal_raises_pause(self):
        runtime = self._make_runtime()
        node = FlowNode(id="ws1", type=NodeType.WAIT_SIGNAL, config={"signal_name": "payment_done"})
        wf = WorkflowDef(id="w4", name="test")
        with pytest.raises(WorkflowSignalPause) as exc_info:
            runtime.exec_node(node, wf)
        assert exc_info.value.signal_name == "payment_done"

    def test_wait_signal_missing_name(self):
        runtime = self._make_runtime()
        node = FlowNode(id="ws2", type=NodeType.WAIT_SIGNAL, config={})
        wf = WorkflowDef(id="w5", name="test")
        with pytest.raises(Exception, match="signal_name"):
            runtime.exec_node(node, wf)


# ── Idempotency in exec_node ─────────────────────────────────────


class TestIdempotencyInExecNode:
    def _make_runtime(self):
        from unittest.mock import MagicMock

        from core.assets.workflows.workflow_node_runtime import WorkflowNodeRuntime

        return WorkflowNodeRuntime(
            workspace_dir="/tmp/test",
            approval_queue=MagicMock(),
            save_workflow=MagicMock(),
            load_workflow=MagicMock(),
            resume_workflow=MagicMock(),
            run_workflow=MagicMock(),
            resolve_var=lambda v, w: v,
            resolve_config=lambda c, w: c,
            evaluate_condition=lambda c, w: True,
            get_predecessors=lambda w, n: [],
            workflow_approval_fingerprint=lambda **kw: "fp",
            log_event=lambda *a: None,
            extra_dispatch=lambda *a: None,
        )

    def test_idempotent_cache_hit(self):
        runtime = self._make_runtime()
        node = FlowNode(id="idem1", type=NodeType.EXEC, config={"command": "echo hi"}, idempotency_key="key1")
        wf = WorkflowDef(id="w6", name="test")
        wf.variables["_idempotent:key1"] = {"cached": True}

        result = runtime.exec_node(node, wf)
        assert result == {"cached": True}
        assert node.status == NodeStatus.COMPLETED
