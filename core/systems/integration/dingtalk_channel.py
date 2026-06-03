"""钉钉渠道适配器。

支持：
- Webhook 模式：接收 HTTP 回调（outgoing 机器人）
- 消息发送（通过 OpenAPI /v1.0/robot/oToMessages/batchSend）
- access_token 自动刷新
- 签名验证

配置项（在 config.json 的 channels.dingtalk 下）：
  app_key      — 应用 AppKey
  app_secret   — 应用 AppSecret
  token        — （可选）outgoing 签名 token
  robot_code   — 机器人编码（用于发送消息）
"""

from __future__ import annotations

import hashlib
import hmac
import base64
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

_DINGTALK_API = "https://api.dingtalk.com"
_OLD_API = "https://oapi.dingtalk.com"
_TOKEN_TTL = 7000


class DingTalkChannel(BaseChannel):
    """钉钉渠道 — 通过 OpenAPI 发送消息，通过 Webhook/Stream 接收消息。"""

    name = "dingtalk"

    def __init__(self, config: ChannelConfig | None = None) -> None:
        super().__init__(config or ChannelConfig(name=self.name, kind=self.name))
        self._app_key = self.config.extra.get("app_key", "") or self.config.app_id or ""
        self._app_secret = self.config.app_secret or ""
        self._robot_code = self.config.extra.get("robot_code", "")
        self._verify_token = self.config.token or ""
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

    # ── Token 管理 ────────────────────────────────────────────────────────

    def _refresh_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        if not self._app_key or not self._app_secret:
            logger.warning("[DingTalk] app_key / app_secret not configured")
            return ""
        try:
            resp = httpx.post(
                f"{_DINGTALK_API}/v1.0/oauth2/accessToken",
                json={"appKey": self._app_key, "appSecret": self._app_secret},
                timeout=10,
            )
            data = resp.json()
            token = data.get("accessToken", "")
            if token:
                self._access_token = token
                self._token_expires_at = time.time() + _TOKEN_TTL
                logger.info("[DingTalk] access_token refreshed")
            else:
                logger.warning("[DingTalk] token refresh failed: %s", data)
        except Exception as exc:
            logger.warning("[DingTalk] token refresh error: %s", exc)
        return self._access_token

    def _auth_headers(self) -> dict[str, str]:
        token = self._refresh_token()
        return {"x-acs-dingtalk-access-token": token, "Content-Type": "application/json"}

    # ── 发送消息 ──────────────────────────────────────────────────────────

    def send_message(self, target_id: str, message: str) -> bool | ChannelSendResult:
        """向用户发送单聊文本消息。target_id 为用户的 staffId。"""
        if not self._robot_code:
            logger.warning("[DingTalk] robot_code not configured, cannot send")
            return ChannelSendResult(success=False, error="robot_code not set")
        body = {
            "robotCode": self._robot_code,
            "userIds": [target_id],
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": message}),
        }
        try:
            resp = httpx.post(
                f"{_DINGTALK_API}/v1.0/robot/oToMessages/batchSend",
                json=body,
                headers=self._auth_headers(),
                timeout=15,
            )
            data = resp.json()
            ok = "processQueryKey" in data
            if not ok:
                logger.warning("[DingTalk] send failed: %s", data)
            return ChannelSendResult(success=ok, raw=data)
        except Exception as exc:
            logger.warning("[DingTalk] send error: %s", exc)
            return ChannelSendResult(success=False, error=str(exc))

    # ── 接收消息 ──────────────────────────────────────────────────────────

    def verify_webhook(self, request: ChannelWebhookRequest) -> ChannelWebhookVerification | None:
        """验证钉钉 outgoing 机器人签名。"""
        if not self._verify_token:
            return None
        timestamp = request.headers.get("timestamp", "")
        sign = request.headers.get("sign", "")
        if not timestamp or not sign:
            return None
        string_to_sign = f"{timestamp}\n{self._verify_token}"
        expected = base64.b64encode(
            hmac.HMAC(
                self._verify_token.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        if sign != expected:
            return ChannelWebhookVerification(verified=False, error="signature mismatch")
        return None

    def parse_incoming(self, payload: ChannelWebhookRequest | dict[str, Any]) -> dict[str, Any] | None:
        """解析钉钉 outgoing 机器人回调。"""
        if isinstance(payload, ChannelWebhookRequest):
            data = payload.json_payload or {}
        else:
            data = payload

        msg_type = data.get("msgtype", "")
        if msg_type != "text":
            logger.debug("[DingTalk] ignoring msgtype: %s", msg_type)
            return None

        text_content = data.get("text", {})
        text = text_content.get("content", "").strip() if isinstance(text_content, dict) else ""
        if not text:
            return None

        sender_id = data.get("senderStaffId", "") or data.get("senderId", "")
        conversation_id = data.get("conversationId", "")
        conversation_type = data.get("conversationType", "")

        return {
            "user_id": str(sender_id),
            "message": text,
            "thread_id": f"dingtalk_{conversation_id or sender_id}",
            "channel": "dingtalk",
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "at_users": [u.get("dingtalkId", "") for u in data.get("atUsers", [])],
        }
