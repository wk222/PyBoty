"""
Channels 渠道管理器 — 桥接外部通讯软件

参考 OpenClaw 的 Channel 架构：
允许 PyBot 接收来自外部渠道（Webhook, 飞书, 钉钉, 企业微信, Slack 等）的消息，
并将 Agent 或 Workflow 的执行结果推送到对应的渠道。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .channel_runtime import ChannelRuntime


class BaseChannel(ABC):
    name: str

    @abstractmethod
    def send_message(self, target_id: str, message: str) -> bool:
        """发送消息到渠道。"""

    @abstractmethod
    def parse_incoming(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """解析外部 Webhook 请求为标准消息结构。"""


class WebhookChannel(BaseChannel):
    """通用的 Webhook 渠道（示例）。"""

    name = "webhook"

    def send_message(self, target_id: str, message: str) -> bool:
        print(f"[Channel: {self.name}] 发送给 {target_id}: {message}")
        return True

    def parse_incoming(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        user = payload.get("user")
        text = payload.get("text")
        if user and text:
            return {
                "user_id": str(user),
                "message": str(text),
                "thread_id": f"webhook_{user}",
            }
        return None


class ChannelManager:
    """Registry/orchestration layer for external channels."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.channels: dict[str, BaseChannel] = {}
        self._runtime = ChannelRuntime()
        self._register_builtins()

    def _register_builtins(self) -> None:
        self.register_channel(WebhookChannel())

    def register_channel(self, channel: BaseChannel) -> None:
        self.channels[channel.name] = channel

    def list_channels(self) -> list[str]:
        return sorted(self.channels)

    def set_agent_callback(self, callback: Callable[[str, str], str]) -> None:
        """设置接收到消息后触发的 Agent 回调。"""
        self._runtime.set_agent_callback(callback)

    def handle_incoming(self, channel_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """处理来自外部的 webhook 请求。"""
        channel = self.channels.get(channel_name)
        if channel is None:
            return {"success": False, "error": f"Channel '{channel_name}' not found"}
        return self._runtime.handle_incoming(
            channel_name=channel_name,
            channel=channel,
            payload=payload,
        )
