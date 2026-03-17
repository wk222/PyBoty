from __future__ import annotations

from typing import Any

from core.channel_manager import BaseChannel, ChannelManager


class EchoChannel(BaseChannel):
    name = "echo"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, target_id: str, message: str) -> bool:
        self.sent.append((target_id, message))
        return True

    def parse_incoming(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return payload if payload.get("user_id") else None


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
