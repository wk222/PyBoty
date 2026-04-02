from __future__ import annotations

import asyncio
from typing import Any

from core.channel_manager import BaseChannel, ChannelManager
from core.systems.integration.channel_runtime import ChannelConfig, ChannelWebhookRequest, ChannelWebhookVerification


class EchoChannel(BaseChannel):
    name = "echo"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, target_id: str, message: str) -> bool:
        self.sent.append((target_id, message))
        return True

    def parse_incoming(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return payload if payload.get("user_id") else None


class VerifiedEchoChannel(EchoChannel):
    name = "verified-echo"

    def __init__(self) -> None:
        super().__init__()
        self.config = ChannelConfig(name=self.name, kind=self.name, token="secret-token")

    def verify_webhook(self, request: ChannelWebhookRequest) -> ChannelWebhookVerification | None:
        if request.query_params.get("token") != self.config.token:
            return ChannelWebhookVerification(verified=False, error="bad token")
        if request.method.upper() == "GET":
            return ChannelWebhookVerification(verified=True, challenge="verified")
        return ChannelWebhookVerification(verified=True)

    async def async_send_message(self, target_id: str, message: str, *, message_context=None):  # noqa: ANN001,ARG002
        self.sent.append((target_id, message))
        return True


def test_channel_manager_processes_incoming_message(temp_paths):
    manager = ChannelManager(str(temp_paths.workspace_dir))
    channel = EchoChannel()
    manager.register_channel(channel)
    manager.set_agent_callback(lambda message, thread_id: f"{thread_id}:{message.upper()}")

    result = manager.handle_incoming(
        "echo",
        {"user_id": "u1", "message": "hello", "thread_id": "thread-1"},
    )

    assert result["success"] is True
    assert result["thread_id"] == "thread-1"
    assert channel.sent == [("u1", "thread-1:HELLO")]
    assert "echo" in manager.list_channels()


def test_channel_manager_returns_error_for_unknown_channel(temp_paths):
    manager = ChannelManager(str(temp_paths.workspace_dir))

    result = manager.handle_incoming("missing", {"user_id": "u1"})

    assert result["success"] is False
    assert "not found" in result["error"]


def test_channel_manager_sends_error_reply_when_agent_callback_fails(temp_paths):
    manager = ChannelManager(str(temp_paths.workspace_dir))
    channel = EchoChannel()
    manager.register_channel(channel)

    def explode(message: str, thread_id: str) -> str:
        raise RuntimeError("boom")

    manager.set_agent_callback(explode)
    result = manager.handle_incoming(
        "echo",
        {"user_id": "u1", "message": "hello", "thread_id": "thread-1"},
    )

    assert result["success"] is False
    assert "boom" in result["error"]
    assert channel.sent == [("u1", "处理失败: boom")]


def test_channel_manager_handles_verified_webhook_get(temp_paths):
    manager = ChannelManager(str(temp_paths.workspace_dir))
    manager.register_channel(VerifiedEchoChannel())

    result = asyncio.run(
        manager.handle_webhook(
            "verified-echo",
            ChannelWebhookRequest(method="GET", query_params={"token": "secret-token"}),
        )
    )

    assert result["success"] is True
    assert result["status"] == "verified"
    assert result["http_response"]["body"] == "verified"


def test_channel_manager_rejects_invalid_webhook_signature(temp_paths):
    manager = ChannelManager(str(temp_paths.workspace_dir))
    manager.register_channel(VerifiedEchoChannel())

    result = asyncio.run(
        manager.handle_webhook(
            "verified-echo",
            ChannelWebhookRequest(
                method="POST",
                query_params={"token": "bad-token"},
                json_payload={"user_id": "u1", "message": "hello", "thread_id": "thread-1"},
                content_type="application/json",
            ),
        )
    )

    assert result["success"] is False
    assert result["error"] == "bad token"


def test_channel_manager_preview_and_route_callback(temp_paths):
    manager = ChannelManager(
        str(temp_paths.workspace_dir),
        channel_routes=[
            {
                "name": "ops-exec",
                "channel": "echo",
                "starts_with": "/exec",
                "mode": "admin",
                "thread_template": "ops:{user_id}",
            }
        ],
    )
    channel = EchoChannel()
    manager.register_channel(channel)

    preview = manager.preview_route(
        "echo",
        {"user_id": "u1", "message": "/exec hello", "thread_id": "thread-1"},
    )

    assert preview["matched"] is True
    assert preview["decision"]["mode"] == "admin"
    assert preview["decision"]["thread_id"] == "ops:u1"

    manager.set_route_callback(
        lambda channel_name, message, decision: (
            f"{channel_name}:{decision.mode}:{decision.thread_id}:{message.message.upper()}"
        )
    )

    result = manager.handle_incoming(
        "echo",
        {"user_id": "u1", "message": "/exec hello", "thread_id": "thread-1"},
    )

    assert result["success"] is True
    assert result["route"]["mode"] == "admin"
    assert result["route"]["thread_id"] == "ops:u1"
    assert channel.sent == [("u1", "echo:admin:ops:u1:/EXEC HELLO")]
