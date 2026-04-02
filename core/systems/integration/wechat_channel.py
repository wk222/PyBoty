"""WeChat Official Account channel adapter."""

from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from html import escape
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


def _xml_to_dict(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    return {child.tag: child.text or "" for child in root}


def _sha1_signature(*parts: str) -> str:
    payload = "".join(sorted(parts))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _build_text_reply_xml(*, to_user: str, from_user: str, content: str) -> str:
    escaped = escape(content)
    timestamp = int(time.time())
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{timestamp}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{escaped}]]></Content>"
        "</xml>"
    )


class WeChatOfficialChannel(BaseChannel):
    """微信公众号 / 服务号 adapter."""

    name = "wechat"

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    def verify_webhook(self, request: ChannelWebhookRequest) -> ChannelWebhookVerification | None:
        token = self.config.token
        if not token:
            return ChannelWebhookVerification(verified=False, error="WeChat channel token is not configured")

        signature = request.query_params.get("signature", "")
        timestamp = request.query_params.get("timestamp", "")
        nonce = request.query_params.get("nonce", "")
        if not signature or not timestamp or not nonce:
            return ChannelWebhookVerification(verified=False, error="Missing WeChat webhook signature parameters")

        expected = _sha1_signature(token, timestamp, nonce)
        if expected != signature:
            return ChannelWebhookVerification(verified=False, error="Invalid WeChat webhook signature")

        if request.method.upper() == "GET":
            return ChannelWebhookVerification(
                verified=True,
                challenge=request.query_params.get("echostr", ""),
                content_type="text/plain",
            )
        return ChannelWebhookVerification(verified=True)

    def send_message(self, target_id: str, message: str) -> bool:
        print(f"[Channel: {self.name}] 准备回复给 {target_id}: {message}")
        return True

    async def async_send_message(
        self,
        target_id: str,
        message: str,
        *,
        message_context: ChannelMessage | None = None,
    ) -> bool | ChannelSendResult:
        metadata = message_context.metadata if message_context is not None else {}
        to_user = str(metadata.get("from_user") or target_id)
        from_user = str(metadata.get("to_user") or self.config.app_id or "pybot")

        if self.config.reply_mode == "passive" or not (self.config.app_id and self.config.app_secret):
            return ChannelSendResult.reply(
                _build_text_reply_xml(to_user=to_user, from_user=from_user, content=message),
                content_type="application/xml",
                metadata={"delivery": "passive_reply"},
            )

        access_token = await self._get_access_token()
        api_base = self.config.api_base or "https://api.weixin.qq.com"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{api_base}/cgi-bin/message/custom/send",
                params={"access_token": access_token},
                json={
                    "touser": target_id,
                    "msgtype": "text",
                    "text": {"content": message},
                },
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("errcode", 0) != 0:
            return ChannelSendResult.failed(f"WeChat send failed: {payload}")
        return ChannelSendResult.ok(metadata={"delivery": "custom_api"})

    def parse_incoming(self, payload: ChannelWebhookRequest | dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(payload, ChannelWebhookRequest):
            if payload.method.upper() == "GET":
                return None
            xml_text = payload.text_body
        else:
            xml_text = str(payload.get("xml") or "")

        if not xml_text.strip():
            return None

        message = _xml_to_dict(xml_text)
        if message.get("MsgType") != "text":
            return None

        from_user = message.get("FromUserName", "").strip()
        to_user = message.get("ToUserName", "").strip()
        content = message.get("Content", "").strip()
        if not from_user or not content:
            return None

        return {
            "user_id": from_user,
            "message": content,
            "thread_id": f"wechat:{from_user}",
            "metadata": {
                "channel": self.name,
                "from_user": from_user,
                "to_user": to_user,
                "message_type": message.get("MsgType", ""),
                "msg_id": message.get("MsgId", ""),
            },
        }

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        if not self.config.app_id or not self.config.app_secret:
            raise RuntimeError("WeChat app_id/app_secret are required for outbound API messaging")

        api_base = self.config.api_base or "https://api.weixin.qq.com"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{api_base}/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self.config.app_id,
                    "secret": self.config.app_secret,
                },
            )
            response.raise_for_status()
            payload = response.json()

        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"WeChat access_token fetch failed: {payload}")
        expires_in = int(payload.get("expires_in", 7200))
        self._access_token = token
        self._access_token_expires_at = now + max(expires_in - 120, 60)
        return token
