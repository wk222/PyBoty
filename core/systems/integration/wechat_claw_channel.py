"""微信个人号（ClawBot / iLink）渠道适配器。

基于 weixin-bot 项目的 iLink API，实现：
- QR 码登录（生成二维码 URL → 用户微信扫码 → 获取 bot_token）
- 长轮询接收消息（/ilink/bot/getupdates）
- 文本消息发送（/ilink/bot/sendmessage）
- 凭证持久化（~/.pybot/wechat_claw_credentials.json）

配置项（在 config.json 的 channels.wechat_claw 下）：
  base_url       — iLink API 地址（默认 https://ilinkai.weixin.qq.com）
  credential_dir — 凭证存储目录（默认 ~/.pybot）
  poll_timeout   — 长轮询超时（默认 40s）
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .channel_manager import BaseChannel
from .channel_runtime import (
    ChannelConfig,
    ChannelMessage,
    ChannelSendResult,
    ChannelWebhookRequest,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
_CHANNEL_VERSION = "1.0.0"
_DEFAULT_CREDENTIAL_DIR = Path.home() / ".pybot"
_QR_POLL_INTERVAL = 2.0


@dataclass
class ClawCredentials:
    token: str
    base_url: str
    account_id: str
    user_id: str


class WeChatClawChannel(BaseChannel):
    """微信个人号渠道 — 通过 iLink API 实现扫码登录和消息收发。"""

    name = "wechat_claw"

    def __init__(self, config: ChannelConfig | None = None) -> None:
        super().__init__(config or ChannelConfig(name=self.name, kind=self.name))
        self._base_url = (self.config.api_base or _DEFAULT_BASE_URL).rstrip("/")
        cred_dir = self.config.extra.get("credential_dir", "")
        self._credential_path = (
            Path(cred_dir) / "wechat_claw_credentials.json"
            if cred_dir
            else _DEFAULT_CREDENTIAL_DIR / "wechat_claw_credentials.json"
        )
        self._poll_timeout = int(self.config.extra.get("poll_timeout", 40))
        self._credentials: ClawCredentials | None = None
        self._cursor: str = ""
        self._context_tokens: dict[str, str] = {}
        self._running = False
        self._poll_thread: threading.Thread | None = None
        self._agent_callback: Any = None
        self._qr_status: dict[str, Any] = {}

    # ── 凭证管理 ──────────────────────────────────────────────────────────

    def _load_credentials(self) -> ClawCredentials | None:
        try:
            data = json.loads(self._credential_path.read_text(encoding="utf-8"))
            return ClawCredentials(
                token=data["token"],
                base_url=data.get("baseUrl", data.get("base_url", self._base_url)),
                account_id=data.get("accountId", data.get("account_id", "")),
                user_id=data.get("userId", data.get("user_id", "")),
            )
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return None

    def _save_credentials(self, creds: ClawCredentials) -> None:
        self._credential_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "token": creds.token,
            "baseUrl": creds.base_url,
            "accountId": creds.account_id,
            "userId": creds.user_id,
        }
        self._credential_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("[WeChatClaw] credentials saved to %s", self._credential_path)

    def _clear_credentials(self) -> None:
        self._credential_path.unlink(missing_ok=True)
        self._credentials = None
        self._cursor = ""
        self._context_tokens.clear()

    # ── HTTP 工具 ─────────────────────────────────────────────────────────

    def _base_info(self) -> dict[str, str]:
        return {"channel_version": _CHANNEL_VERSION}

    def _random_uin(self) -> str:
        value = int.from_bytes(os.urandom(4), "big")
        return base64.b64encode(str(value).encode("utf-8")).decode("ascii")

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token}",
            "X-WECHAT-UIN": self._random_uin(),
        }

    def _api_post(self, endpoint: str, body: dict, token: str, timeout: int = 15) -> dict[str, Any]:
        url = f"{self._base_url}{endpoint}"
        resp = httpx.post(url, json=body, headers=self._auth_headers(token), timeout=timeout)
        data = resp.json()
        if resp.status_code >= 400:
            raise RuntimeError(f"API error {resp.status_code}: {data.get('errmsg', '')}")
        ret = data.get("ret", 0)
        if isinstance(ret, int) and ret != 0:
            code = data.get("errcode", ret)
            if code == -14:
                raise SessionExpiredError(f"Session expired: {data.get('errmsg', '')}")
            raise RuntimeError(f"API error {code}: {data.get('errmsg', '')}")
        return data

    # ── QR 码登录 ─────────────────────────────────────────────────────────

    def fetch_qr_code(self) -> dict[str, Any]:
        """获取登录二维码。返回 {"qrcode": "...", "qrcode_img_content": "url_or_data"}"""
        url = f"{self._base_url}/ilink/bot/get_bot_qrcode?bot_type=3"
        resp = httpx.get(url, timeout=15)
        data = resp.json()
        self._qr_status = {"status": "wait", "qrcode": data.get("qrcode", "")}
        return data

    def poll_qr_status(self, qrcode: str) -> dict[str, Any]:
        """轮询二维码扫描状态。返回 status: wait/scaned/confirmed/expired"""
        from urllib.parse import quote
        url = f"{self._base_url}/ilink/bot/get_qrcode_status?qrcode={quote(qrcode, safe='')}"
        resp = httpx.get(url, headers={"iLink-App-ClientVersion": "1"}, timeout=15)
        data = resp.json()
        self._qr_status = data
        return data

    def login(self, force: bool = False) -> ClawCredentials | None:
        """同步登录流程：加载已有凭证或触发 QR 扫码。

        Returns None 表示需要用户扫码（通过 API 端点轮询状态）。
        """
        if not force:
            existing = self._load_credentials()
            if existing:
                self._credentials = existing
                self._base_url = existing.base_url
                logger.info("[WeChatClaw] loaded cached credentials for user %s", existing.user_id)
                return existing
        return None

    def confirm_login(self, status_data: dict[str, Any]) -> ClawCredentials | None:
        """从 QR 状态轮询结果中提取凭证并保存。"""
        if status_data.get("status") != "confirmed":
            return None
        token = status_data.get("bot_token")
        account_id = status_data.get("ilink_bot_id")
        user_id = status_data.get("ilink_user_id")
        if not token or not account_id or not user_id:
            return None
        creds = ClawCredentials(
            token=token,
            base_url=status_data.get("baseurl") or self._base_url,
            account_id=account_id,
            user_id=user_id,
        )
        self._credentials = creds
        self._base_url = creds.base_url
        self._save_credentials(creds)
        return creds

    @property
    def is_logged_in(self) -> bool:
        return self._credentials is not None

    # ── 发送消息 ──────────────────────────────────────────────────────────

    def send_message(self, target_id: str, message: str) -> bool | ChannelSendResult:
        if not self._credentials:
            return ChannelSendResult(success=False, error="Not logged in")
        context_token = self._context_tokens.get(target_id, "")
        if not context_token:
            return ChannelSendResult(success=False, error=f"No context token for user {target_id}")

        chunks = _chunk_text(message, 2000)
        for chunk in chunks:
            msg_body = {
                "from_user_id": "",
                "to_user_id": target_id,
                "client_id": str(uuid4()),
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": chunk}}],
            }
            try:
                self._api_post(
                    "/ilink/bot/sendmessage",
                    {"msg": msg_body, "base_info": self._base_info()},
                    self._credentials.token,
                )
            except Exception as exc:
                logger.warning("[WeChatClaw] send error: %s", exc)
                return ChannelSendResult(success=False, error=str(exc))
        return ChannelSendResult(success=True)

    # ── 接收消息 ──────────────────────────────────────────────────────────

    def parse_incoming(self, payload: ChannelWebhookRequest | dict[str, Any]) -> dict[str, Any] | None:
        """解析 iLink 消息格式为标准消息结构。"""
        if isinstance(payload, ChannelWebhookRequest):
            data = payload.json_payload or {}
        else:
            data = payload

        msg_type = data.get("message_type", 0)
        if msg_type != 1:  # 只处理用户消息
            return None

        items = data.get("item_list", [])
        text_parts = []
        for item in items:
            if item.get("type") == 1:
                text_parts.append(item.get("text_item", {}).get("text", ""))
        text = "\n".join(t for t in text_parts if t)
        if not text:
            return None

        user_id = data.get("from_user_id", "")
        context_token = data.get("context_token", "")
        if user_id and context_token:
            self._context_tokens[user_id] = context_token

        return {
            "user_id": user_id,
            "message": text,
            "thread_id": f"wechat_claw_{user_id}",
            "channel": "wechat_claw",
            "context_token": context_token,
            "message_id": data.get("message_id", ""),
        }

    # ── 长轮询循环 ────────────────────────────────────────────────────────

    def start_polling(self, agent_callback: Any = None) -> None:
        """启动后台长轮询线程。

        Parameters
        ----------
        agent_callback:
            接收 (thread_id, user_message) 返回 reply 的回调函数。
        """
        if self._running:
            return
        if not self._credentials:
            logger.warning("[WeChatClaw] not logged in, cannot start polling")
            return
        self._agent_callback = agent_callback
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="wechat_claw_poll")
        self._poll_thread.start()
        logger.info("[WeChatClaw] polling started")

    def stop_polling(self) -> None:
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
            self._poll_thread = None
        logger.info("[WeChatClaw] polling stopped")

    def _poll_loop(self) -> None:
        retry_delay = 1.0
        while self._running and self._credentials:
            try:
                data = self._api_post(
                    "/ilink/bot/getupdates",
                    {"get_updates_buf": self._cursor, "base_info": self._base_info()},
                    self._credentials.token,
                    timeout=self._poll_timeout + 5,
                )
                self._cursor = data.get("get_updates_buf", self._cursor)
                retry_delay = 1.0

                for raw_msg in data.get("msgs", []):
                    self._remember_context(raw_msg)
                    parsed = self.parse_incoming(raw_msg)
                    if parsed and self._agent_callback:
                        try:
                            reply = self._agent_callback(parsed["thread_id"], parsed["message"])
                            if reply:
                                self.send_message(parsed["user_id"], reply)
                        except Exception as exc:
                            logger.error("[WeChatClaw] callback error: %s", exc)
            except SessionExpiredError:
                logger.warning("[WeChatClaw] session expired, clearing credentials")
                self._clear_credentials()
                break
            except Exception as exc:
                logger.warning("[WeChatClaw] poll error: %s (retry in %.1fs)", exc, retry_delay)
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 10.0)

    def _remember_context(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("message_type", 0)
        if msg_type == 1:
            user_id = msg.get("from_user_id", "")
        else:
            user_id = msg.get("to_user_id", "")
        context_token = msg.get("context_token", "")
        if user_id and context_token:
            self._context_tokens[user_id] = context_token


class SessionExpiredError(RuntimeError):
    """iLink session token 过期。"""


def _chunk_text(text: str, limit: int) -> list[str]:
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]
