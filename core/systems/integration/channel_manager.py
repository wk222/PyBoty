"""
Channels 渠道管理器 — 桥接外部通讯软件

允许 PyBot 接收来自外部渠道（Webhook, 微信公众号, 企业微信等）的消息，
并将 Agent 或 Workflow 的执行结果推送到对应的渠道。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .channel_runtime import (
    ChannelConfig,
    ChannelMessage,
    ChannelRouteDecision,
    ChannelRuntime,
    ChannelSendResult,
    ChannelWebhookRequest,
    ChannelWebhookVerification,
)


class BaseChannel(ABC):
    name: str

    def __init__(self, config: ChannelConfig | None = None) -> None:
        self.config = config or ChannelConfig(
            name=getattr(self, "name", "channel"),
            kind=getattr(self, "name", "channel"),
        )

    def verify_webhook(self, request: ChannelWebhookRequest) -> ChannelWebhookVerification | None:  # noqa: ARG002
        """验证渠道回调请求；默认无需验证。"""
        return None

    async def async_send_message(
        self,
        target_id: str,
        message: str,
        *,
        message_context: ChannelMessage | None = None,  # noqa: ARG002
    ) -> bool | ChannelSendResult:
        """异步发送消息；默认复用同步实现。"""
        return self.send_message(target_id, message)

    @abstractmethod
    def send_message(self, target_id: str, message: str) -> bool | ChannelSendResult:
        """发送消息到渠道。"""

    @abstractmethod
    def parse_incoming(self, payload: ChannelWebhookRequest | dict[str, Any]) -> dict[str, Any] | None:
        """解析外部请求为标准消息结构。"""


class WebhookChannel(BaseChannel):
    """通用的 Webhook 渠道（示例）。"""

    name = "webhook"

    def __init__(self, config: ChannelConfig | None = None) -> None:
        super().__init__(config or ChannelConfig(name=self.name, kind=self.name))

    def send_message(self, target_id: str, message: str) -> bool:
        print(f"[Channel: {self.name}] 发送给 {target_id}: {message}")
        return True

    def parse_incoming(self, payload: ChannelWebhookRequest | dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(payload, ChannelWebhookRequest):
            data = payload.json_payload or {}
        else:
            data = payload
        user = data.get("user") or data.get("user_id")
        text = data.get("text") or data.get("message")
        thread_id = data.get("thread_id") or (f"webhook_{user}" if user else "")
        if user and text:
            return {
                "user_id": str(user),
                "message": str(text),
                "thread_id": str(thread_id),
            }
        return None


class ChannelManager:
    """Registry/orchestration layer for external channels."""

    def __init__(
        self,
        workspace_dir: str,
        channel_configs: dict[str, Any] | None = None,
        channel_routes: list[dict[str, Any]] | None = None,
    ):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.channels: dict[str, BaseChannel] = {}
        self.channel_configs = channel_configs if isinstance(channel_configs, dict) else {}
        self._runtime = ChannelRuntime(channel_routes)
        self._register_builtins()

    def _register_builtins(self) -> None:
        self.register_channel(
            WebhookChannel(
                ChannelConfig.from_dict(
                    "webhook",
                    self.channel_configs.get("webhook"),
                    default_kind="webhook",
                )
            )
        )

        wechat_cfg = self.channel_configs.get("wechat")
        if isinstance(wechat_cfg, dict) and wechat_cfg.get("enabled", True):
            from .wechat_channel import WeChatOfficialChannel

            self.register_channel(
                WeChatOfficialChannel(ChannelConfig.from_dict("wechat", wechat_cfg, default_kind="wechat"))
            )

        wecom_cfg = self.channel_configs.get("wecom")
        if isinstance(wecom_cfg, dict) and wecom_cfg.get("enabled", True):
            from .wecom_channel import WeComChannel

            self.register_channel(WeComChannel(ChannelConfig.from_dict("wecom", wecom_cfg, default_kind="wecom")))

    def register_channel(self, channel: BaseChannel) -> None:
        self.channels[channel.name] = channel

    def get_channel(self, channel_name: str) -> BaseChannel | None:
        return self.channels.get(channel_name)

    def list_channels(self) -> list[str]:
        return sorted(self.channels)

    def set_agent_callback(self, callback: Callable[[str, str], str]) -> None:
        """设置接收到消息后触发的 Agent 回调。"""
        self._runtime.set_agent_callback(callback)

    def set_route_callback(
        self,
        callback: Callable[[str, ChannelMessage, ChannelRouteDecision], str],
    ) -> None:
        """Set a richer callback that can route by mode/workflow."""
        self._runtime.set_route_callback(callback)

    def list_routes(self) -> list[dict[str, Any]]:
        return self._runtime.list_routes()

    def preview_route(self, channel_name: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        return self._runtime.preview_route(channel_name, payload)

    def handle_incoming(self, channel_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """处理传统 JSON webhook 请求。"""
        channel = self.channels.get(channel_name)
        if channel is None:
            return {"success": False, "error": f"Channel '{channel_name}' not found"}
        return self._runtime.handle_incoming(
            channel_name=channel_name,
            channel=channel,
            payload=payload,
        )

    async def handle_webhook(self, channel_name: str, request: ChannelWebhookRequest) -> dict[str, Any]:
        """处理完整 webhook 请求（支持验证、XML、签名等）。"""
        channel = self.channels.get(channel_name)
        if channel is None:
            return {"success": False, "error": f"Channel '{channel_name}' not found"}
        return await self._runtime.handle_webhook(
            channel_name=channel_name,
            channel=channel,
            request=request,
        )
