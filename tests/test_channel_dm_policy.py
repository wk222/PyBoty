"""Tests for channel DM access policy and multi-tenant isolation (OpenClaw-inspired)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.systems.integration.channels.channel_runtime import ChannelConfig, ChannelMessage, ChannelRuntime
from core.systems.integration.channels.dm_policy import (
    ChannelPairingStore,
    evaluate_dm_access,
)
from core.systems.integration.channels.channel_manager import WebhookChannel


@pytest.fixture
def pairing_store(tmp_path: Path) -> ChannelPairingStore:
    return ChannelPairingStore(tmp_path / "pairing.json")


def test_open_policy_allows_any_user():
    config = ChannelConfig(name="webhook", kind="webhook", dm_policy="open")
    decision = evaluate_dm_access(
        channel_name="webhook",
        user_id="user-1",
        config=config,
        pairing_store=None,
    )
    assert decision.allowed is True


def test_allowlist_blocks_unknown_user():
    config = ChannelConfig(name="feishu", kind="feishu", dm_policy="allowlist", allow_from=("alice",))
    decision = evaluate_dm_access(
        channel_name="feishu",
        user_id="bob",
        config=config,
        pairing_store=None,
    )
    assert decision.allowed is False
    assert decision.reason == "not_in_allowlist"


def test_pairing_generates_code_and_approve_flow(pairing_store: ChannelPairingStore):
    config = ChannelConfig(name="wecom", kind="wecom", dm_policy="pairing")
    first = evaluate_dm_access(
        channel_name="wecom",
        user_id="user-42",
        config=config,
        pairing_store=pairing_store,
    )
    assert first.allowed is False
    assert first.pairing_code
    approved_user = pairing_store.confirm_pairing("wecom", first.pairing_code)
    assert approved_user == "user-42"
    second = evaluate_dm_access(
        channel_name="wecom",
        user_id="user-42",
        config=config,
        pairing_store=pairing_store,
    )
    assert second.allowed is True


def test_channel_runtime_blocks_allowlist_inbound(tmp_path: Path):
    runtime = ChannelRuntime(workspace_dir=tmp_path)
    runtime.set_agent_callback(lambda _msg, _tid: "should not run")
    channel = WebhookChannel(
        ChannelConfig.from_dict(
            "webhook",
            {"dm_policy": "allowlist", "allow_from": ["allowed-user"]},
            default_kind="webhook",
        )
    )
    result = runtime.handle_incoming(
        channel_name="webhook",
        channel=channel,
        payload={"user_id": "stranger", "message": "hi", "thread_id": "t-1"},
    )
    assert result["success"] is False
    assert result["error"] == "not_in_allowlist"


def test_channel_routing_isolation_by_group_or_user(tmp_path: Path):
    """Verify that thread_id rendering dynamically isolates threads based on user/group metadata."""
    runtime = ChannelRuntime(workspace_dir=tmp_path)
    
    # 1. Test user-isolated template
    rule_user = ChannelConfig.from_dict("feishu", {
        "thread_template": "feishu_user_{user_id}"
    })
    msg = ChannelMessage(user_id="user_abc", message="hello", thread_id="thread_123")
    
    # Simulate routing logic
    from core.systems.integration.channels.channel_runtime import ChannelRouteRule
    rule = ChannelRouteRule(name="user_route", thread_template="feishu_user_{user_id}")
    rendered_thread = rule.render_thread_id("feishu", msg)
    assert rendered_thread == "feishu_user_user_abc"

    # 2. Test group-isolated template with metadata
    msg_group = ChannelMessage(
        user_id="user_abc", 
        message="hello", 
        thread_id="thread_123",
        metadata={"chat_id": "group_xyz"}
    )
    rule_group = ChannelRouteRule(name="group_route", thread_template="feishu_group_{chat_id}")
    rendered_group_thread = rule_group.render_thread_id("feishu", msg_group)
    assert rendered_group_thread == "feishu_group_group_xyz"
