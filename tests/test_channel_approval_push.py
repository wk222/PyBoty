"""Tests for channel-based approval workflows and notifications."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.systems.integration.channels.channel_runtime import ChannelMessage, ChannelRouteDecision
from core.systems.governance.approval_queue import ApprovalQueue, InterruptKind
from web.routers.gateway import _channel_route_callback


class FakeChannel:
    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []

    def send_message(self, target_id: str, message: str) -> bool:
        self.sent_messages.append((target_id, message))
        return True


class FakeChannelManager:
    def __init__(self, channel):
        self.channel = channel
        self._runtime = SimpleNamespace(_normalize_send_result=lambda r: SimpleNamespace(success=True, error=None, response_body=None))

    def get_channel(self, name: str):
        return self.channel


class FakeSystemAgent:
    def __init__(self, manager):
        self.channel_manager = manager


class FakeAgent:
    def __init__(self, queue):
        self.queue = queue

    def chat(self, message: str) -> str:
        # Trigger an approval request during chat execution
        self.queue.create_request(
            kind=InterruptKind.TOOL_APPROVAL,
            scope="test_thread",
            summary="Run high-risk tool bash",
            prompt="bash: rm -rf /",
            fingerprint="test_thread:bash",
        )
        return "blocked_by_approval"


@pytest.fixture
def mock_services(tmp_path: Path):
    queue = ApprovalQueue(tmp_path / "approvals.json")
    channel = FakeChannel()
    manager = FakeChannelManager(channel)
    system_agent = FakeSystemAgent(manager)
    
    agents = MagicMock()
    agents.get_or_create_mode = lambda mode, thread_id: FakeAgent(queue)

    services = SimpleNamespace(
        approval_queue=queue,
        system_agent=lambda: system_agent,
        agents=agents,
    )
    return services, channel, queue


def test_channel_route_callback_intercepts_and_pushes_approval(mock_services):
    services, channel, queue = mock_services
    message = ChannelMessage(user_id="user_1", message="run bash command", thread_id="thread_1")
    decision = ChannelRouteDecision(target="agent", mode="assistant", thread_id="thread_1")

    # Run chat turn which triggers tool approval
    res = _channel_route_callback(services, "feishu", message, decision)
    assert res == "blocked_by_approval"

    # Verify that Feishu channel received the approval request notification
    assert len(channel.sent_messages) == 1
    target, text = channel.sent_messages[0]
    assert target == "user_1"
    assert "⚠️ 治理中心拦截" in text
    assert "Run high-risk tool bash" in text
    assert "pybot approve" in text


def test_channel_route_callback_resolves_approval_via_im_command(mock_services):
    services, channel, queue = mock_services
    
    # 1. Manually create a pending request
    req = queue.create_request(
        kind=InterruptKind.TOOL_APPROVAL,
        scope="thread_1",
        summary="Run high-risk tool bash",
        prompt="bash: rm -rf /",
        fingerprint="thread_1:bash",
    )
    approval_id = req.approval_id

    # 2. Simulate user replying "pybot approve appr_xxx"
    message = ChannelMessage(user_id="user_1", message=f"pybot approve {approval_id}", thread_id="thread_1")
    decision = ChannelRouteDecision(target="agent", mode="assistant", thread_id="thread_1")

    res = _channel_route_callback(services, "feishu", message, decision)
    assert "✅ 成功将审批请求" in res
    assert approval_id in res

    # Verify queue state updated
    req_updated = queue.get_request(approval_id)
    assert req_updated.status == "approved"
    assert req_updated.approved is True
    assert "channel:feishu:user_1" in req_updated.resolved_by
