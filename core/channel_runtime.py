"""Runtime helpers for inbound/outbound channel processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChannelMessage:
    """Normalized message received from an external channel."""

    user_id: str
    message: str
    thread_id: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> ChannelMessage | None:
        if not isinstance(payload, dict):
            return None
        user_id = str(payload.get("user_id", "")).strip()
        message = str(payload.get("message", "")).strip()
        thread_id = str(payload.get("thread_id", "")).strip()
        if not user_id or not message or not thread_id:
            return None
        return cls(user_id=user_id, message=message, thread_id=thread_id)


class ChannelRuntime:
    """Process normalized channel messages via the configured agent callback."""

    def __init__(self) -> None:
        self._agent_callback: Any = None

    def set_agent_callback(self, callback: Any) -> None:
        self._agent_callback = callback

    def handle_incoming(
        self,
        *,
        channel_name: str,
        channel: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        parsed = ChannelMessage.from_payload(channel.parse_incoming(payload))
        if parsed is None:
            return {"success": False, "error": "Invalid payload format"}

        print(f"[ChannelManager] 收到 {channel_name} 消息: {parsed.message} (User: {parsed.user_id})")

        if self._agent_callback is None:
            return {"success": False, "error": "Agent callback not configured"}

        try:
            response = self._agent_callback(parsed.message, parsed.thread_id)
            channel.send_message(parsed.user_id, response)
            return {
                "success": True,
                "status": "processed",
                "thread_id": parsed.thread_id,
                "response": response,
            }
        except Exception as exc:
            error_msg = f"处理失败: {exc}"
            channel.send_message(parsed.user_id, error_msg)
            return {"success": False, "error": error_msg, "thread_id": parsed.thread_id}
