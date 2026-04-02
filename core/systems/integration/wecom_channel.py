"""WeCom (Enterprise WeChat) channel adapter."""

from __future__ import annotations

import base64
import hashlib
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from Crypto.Cipher import AES

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


def _wecom_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    payload = "".join(sorted([token, timestamp, nonce, encrypted]))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _pkcs7_unpad(data: bytes) -> bytes:
    pad = data[-1]
    if pad < 1 or pad > 32:
        raise ValueError("Invalid PKCS7 padding")
    return data[:-pad]


class WeComCipher:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str) -> None:
        self.token = token
        self.receive_id = receive_id
        self.key = base64.b64decode(f"{encoding_aes_key}=")
        self.iv = self.key[:16]

    def decrypt(self, encrypted: str) -> str:
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        plain = _pkcs7_unpad(cipher.decrypt(base64.b64decode(encrypted)))
        msg_len = struct.unpack(">I", plain[16:20])[0]
        xml_bytes = plain[20 : 20 + msg_len]
        receive_id = plain[20 + msg_len :].decode("utf-8")
        if receive_id != self.receive_id:
            raise ValueError("WeCom receive_id mismatch")
        return xml_bytes.decode("utf-8")


class WeComChannel(BaseChannel):
    """企业微信 adapter."""

    name = "wecom"

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config)
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    def verify_webhook(self, request: ChannelWebhookRequest) -> ChannelWebhookVerification | None:
        if not self.config.token:
            return ChannelWebhookVerification(verified=False, error="WeCom channel token is not configured")

        signature = request.query_params.get("msg_signature", "")
        timestamp = request.query_params.get("timestamp", "")
        nonce = request.query_params.get("nonce", "")
        encrypted = (
            request.query_params.get("echostr", "")
            if request.method.upper() == "GET"
            else self._extract_encrypt(request)
        )
        if not signature or not timestamp or not nonce or not encrypted:
            return ChannelWebhookVerification(verified=False, error="Missing WeCom webhook signature parameters")

        expected = _wecom_signature(self.config.token, timestamp, nonce, encrypted)
        if expected != signature:
            return ChannelWebhookVerification(verified=False, error="Invalid WeCom webhook signature")

        if request.method.upper() == "GET":
            try:
                challenge = self._cipher().decrypt(encrypted)
            except Exception as exc:
                return ChannelWebhookVerification(verified=False, error=f"WeCom challenge decrypt failed: {exc}")
            return ChannelWebhookVerification(verified=True, challenge=challenge, content_type="text/plain")
        return ChannelWebhookVerification(verified=True)

    def send_message(self, target_id: str, message: str) -> bool:
        print(f"[Channel: {self.name}] 准备回复给 {target_id}: {message}")
        return True

    async def async_send_message(
        self,
        target_id: str,
        message: str,
        *,
        message_context: ChannelMessage | None = None,  # noqa: ARG002
    ) -> bool | ChannelSendResult:
        access_token = await self._get_access_token()
        api_base = self.config.api_base or "https://qyapi.weixin.qq.com"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{api_base}/cgi-bin/message/send",
                params={"access_token": access_token},
                json={
                    "touser": target_id,
                    "msgtype": "text",
                    "agentid": int(self.config.agent_id or 0),
                    "text": {"content": message},
                    "safe": 0,
                },
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("errcode", 0) != 0:
            return ChannelSendResult.failed(f"WeCom send failed: {payload}")
        return ChannelSendResult.reply("success", metadata={"delivery": "send_api"})

    def parse_incoming(self, payload: ChannelWebhookRequest | dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(payload, ChannelWebhookRequest):
            if payload.method.upper() == "GET":
                return None
            encrypted = self._extract_encrypt(payload)
            if not encrypted:
                return None
            xml_text = self._cipher().decrypt(encrypted)
        else:
            xml_text = str(payload.get("xml") or "")

        message = _xml_to_dict(xml_text)
        if message.get("MsgType") != "text":
            return None
        from_user = message.get("FromUserName", "").strip()
        content = message.get("Content", "").strip()
        if not from_user or not content:
            return None
        return {
            "user_id": from_user,
            "message": content,
            "thread_id": f"wecom:{from_user}",
            "metadata": {
                "channel": self.name,
                "from_user": from_user,
                "to_user": message.get("ToUserName", "").strip(),
                "agent_id": message.get("AgentID", "").strip(),
                "msg_id": message.get("MsgId", "").strip(),
            },
        }

    def _cipher(self) -> WeComCipher:
        if not self.config.token or not self.config.encoding_aes_key or not self.config.corp_id:
            raise RuntimeError("WeCom token / encoding_aes_key / corp_id are required")
        return WeComCipher(self.config.token, self.config.encoding_aes_key, self.config.corp_id)

    def _extract_encrypt(self, request: ChannelWebhookRequest) -> str:
        try:
            if request.text_body.strip():
                message = _xml_to_dict(request.text_body)
                return message.get("Encrypt", "")
        except Exception:
            return ""
        return ""

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        if not self.config.corp_id or not self.config.secret:
            raise RuntimeError("WeCom corp_id/secret are required for outbound messaging")

        api_base = self.config.api_base or "https://qyapi.weixin.qq.com"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{api_base}/cgi-bin/gettoken",
                params={"corpid": self.config.corp_id, "corpsecret": self.config.secret},
            )
            response.raise_for_status()
            payload = response.json()

        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"WeCom access_token fetch failed: {payload}")
        expires_in = int(payload.get("expires_in", 7200))
        self._access_token = token
        self._access_token_expires_at = now + max(expires_in - 120, 60)
        return token
