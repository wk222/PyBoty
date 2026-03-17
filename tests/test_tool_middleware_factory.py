from __future__ import annotations

from core.agent_control import AgentControlPolicy
from core.approval_queue import ApprovalQueue
from core.tool_middleware import DynamicToolMiddleware
from core.tool_middleware_factory import (
    build_tool_middleware_components,
    create_decorator_middleware,
    create_tool_middleware,
)
from core.tool_storage import ToolStorage


def test_tool_middleware_factory_builds_runtime_components(tmp_path):
    storage = ToolStorage(str(tmp_path / "tools"))
    components = build_tool_middleware_components(
        tool_storage=storage,
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        approval_queue=ApprovalQueue(),
        approval_scope="root:test",
    )

    assert components.control_policy.mode == "balanced"
    assert components.inventory.tool_storage is storage
    assert components.tool_call_runtime is not None
    assert components.model_runtime is not None


def test_tool_middleware_factory_creates_middleware_and_decorator_hooks(tmp_path):
    storage = ToolStorage(str(tmp_path / "tools"))

    middleware = create_tool_middleware(
        tool_storage=storage,
        control_policy=AgentControlPolicy.from_config({"mode": "open"}),
        approval_queue=ApprovalQueue(),
        approval_scope="root:test",
    )
    decorators = create_decorator_middleware(storage)

    assert isinstance(middleware, DynamicToolMiddleware)
    assert len(decorators) == 2
