"""飞书（Lark/Feishu）渠道适配器。

支持：
- Webhook 模式：接收事件订阅 HTTP 回调
- 文本消息发送（通过 OpenAPI /im/v1/messages）
- tenant_access_token 自动刷新（2h 有效期）
- URL 验证（challenge-response）

配置项（在 config.json 的 channels.feishu 下）：
  app_id       — 应用 App ID
  app_secret   — 应用 App Secret
  token        — 事件订阅的 Verification Token
  encrypt_key  — （可选）事件加密密钥
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx

from .channel_manager import BaseChannel
from .channel_runtime import (
    ChannelConfig,
    ChannelMessage,
    ChannelSendResult,
    ChannelWebhookRequest,
    ChannelWebhookVerification,
)

logger = logging.getLogger(__name__)

_FEISHU_API = "https://open.feishu.cn/open-apis"
_TOKEN_TTL = 7000  # tenant_access_token 有效期 7200s，提前 200s 刷新


class FeishuChannel(BaseChannel):
    """飞书渠道 — 通过 OpenAPI 发送消息，通过 Webhook 接收事件。"""

    name = "feishu"

    def __init__(self, config: ChannelConfig | None = None) -> None:
        super().__init__(config or ChannelConfig(name=self.name, kind=self.name))
        self._app_id = self.config.app_id or ""
        self._app_secret = self.config.app_secret or ""
        self._verify_token = self.config.token or ""
        self._encrypt_key = self.config.extra.get("encrypt_key", "")
        self._tenant_token: str = ""
        self._token_expires_at: float = 0.0

    # ── Token 管理 ────────────────────────────────────────────────────────

    def _refresh_token(self) -> str:
        """获取 / 刷新 tenant_access_token。"""
        if self._tenant_token and time.time() < self._token_expires_at:
            return self._tenant_token
        if not self._app_id or not self._app_secret:
            logger.warning("[Feishu] app_id / app_secret not configured")
            return ""
        try:
            resp = httpx.post(
                f"{_FEISHU_API}/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == 0:
                self._tenant_token = data["tenant_access_token"]
                self._token_expires_at = time.time() + _TOKEN_TTL
                logger.info("[Feishu] tenant_access_token refreshed")
            else:
                logger.warning("[Feishu] token refresh failed: %s", data.get("msg"))
        except Exception as exc:
            logger.warning("[Feishu] token refresh error: %s", exc)
        return self._tenant_token

    def _auth_headers(self) -> dict[str, str]:
        token = self._refresh_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}

    # ── 发送消息 ──────────────────────────────────────────────────────────

    def send_message(self, target_id: str, message: str) -> bool | ChannelSendResult:
        """向 open_id / chat_id 发送文本消息。"""
        receive_id_type = "chat_id" if target_id.startswith("oc_") else "open_id"
        body = {
            "receive_id": target_id,
            "msg_type": "text",
            "content": json.dumps({"text": message}),
        }
        try:
            resp = httpx.post(
                f"{_FEISHU_API}/im/v1/messages",
                params={"receive_id_type": receive_id_type},
                json=body,
                headers=self._auth_headers(),
                timeout=15,
            )
            data = resp.json()
            ok = data.get("code") == 0
            if not ok:
                logger.warning("[Feishu] send failed: %s", data.get("msg"))
            return ChannelSendResult(
                success=ok,
                message_id=data.get("data", {}).get("message_id", ""),
                raw=data,
            )
        except Exception as exc:
            logger.warning("[Feishu] send error: %s", exc)
            return ChannelSendResult(success=False, error=str(exc))

    # ── 接收消息 ──────────────────────────────────────────────────────────

    def verify_webhook(self, request: ChannelWebhookRequest) -> ChannelWebhookVerification | None:
        """处理飞书 URL 验证 challenge。"""
        payload = request.json_payload or {}
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge", "")
            return ChannelWebhookVerification(
                verified=True,
                response_body={"challenge": challenge},
            )
        return None

    def parse_incoming(self, payload: ChannelWebhookRequest | dict[str, Any]) -> dict[str, Any] | None:
        """解析飞书事件回调中的消息。"""
        if isinstance(payload, ChannelWebhookRequest):
            data = payload.json_payload or {}
        else:
            data = payload

        # URL 验证事件不需要处理
        if data.get("type") == "url_verification":
            return None

        header = data.get("header", {})
        event = data.get("event", {})

        # 只处理 im.message.receive_v1 事件
        event_type = header.get("event_type", "")
        if event_type != "im.message.receive_v1":
            logger.debug("[Feishu] ignoring event type: %s", event_type)
            return None

        message = event.get("message", {})
        sender = event.get("sender", {}).get("sender_id", {})

        msg_type = message.get("message_type", "")
        if msg_type != "text":
            logger.debug("[Feishu] ignoring message type: %s", msg_type)
            return None

        try:
            content = json.loads(message.get("content", "{}"))
            text = content.get("text", "")
        except (json.JSONDecodeError, TypeError):
            text = ""

        if not text:
            return None

        open_id = sender.get("open_id", "")
        chat_id = message.get("chat_id", "")

        return {
            "user_id": open_id,
            "message": text,
            "thread_id": f"feishu_{chat_id or open_id}",
            "channel": "feishu",
            "chat_id": chat_id,
            "message_id": message.get("message_id", ""),
            "chat_type": message.get("chat_type", ""),
        }
