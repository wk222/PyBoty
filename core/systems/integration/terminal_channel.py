"""Terminal 渠道 — stdin/stdout 交互式调试渠道。

用途：
- 本地开发调试时直接在终端与 Agent 对话
- 复用 BaseChannel 接口，通过 ChannelManager 统一管理

启动方式：
  channel_manager.get_channel("terminal").run_loop(callback)
"""

from __future__ import annotations

import sys
import logging
from collections.abc import Callable
from typing import Any

from .channel_manager import BaseChannel
from .channel_runtime import (
    ChannelConfig,
    ChannelSendResult,
    ChannelWebhookRequest,
)

logger = logging.getLogger(__name__)


class TerminalChannel(BaseChannel):
    """终端交互渠道 — 用于开发调试。"""

    name = "terminal"

    def __init__(self, config: ChannelConfig | None = None) -> None:
        super().__init__(config or ChannelConfig(name=self.name, kind=self.name))

    def send_message(self, target_id: str, message: str) -> bool | ChannelSendResult:
        """输出到 stdout。"""
        print(f"\n🤖 {message}\n")
        return True

    def parse_incoming(self, payload: ChannelWebhookRequest | dict[str, Any]) -> dict[str, Any] | None:
        """解析终端输入。"""
        if isinstance(payload, dict):
            text = payload.get("text", "") or payload.get("message", "")
        elif isinstance(payload, ChannelWebhookRequest):
            text = (payload.json_payload or {}).get("text", "")
        else:
            text = ""
        if not text:
            return None
        return {
            "user_id": "terminal_user",
            "message": text.strip(),
            "thread_id": "terminal_session",
            "channel": "terminal",
        }

    def run_loop(self, callback: Callable[[str, str], str] | None = None) -> None:
        """阻塞式 REPL 循环 — 仅用于本地调试。

        Parameters
        ----------
        callback:
            接收 (thread_id, user_message) 返回 agent_reply 的函数。
            若为 None 则只回显输入。
        """
        print("═" * 50)
        print("  PyBot Terminal Channel")
        print("  输入消息开始对话，Ctrl+C 退出")
        print("═" * 50)
        try:
            while True:
                try:
                    user_input = input("\n👤 ").strip()
                except EOFError:
                    break
                if not user_input:
                    continue
                if user_input.lower() in ("/quit", "/exit", "exit", "quit"):
                    break
                if callback:
                    reply = callback("terminal_session", user_input)
                    self.send_message("terminal_user", reply)
                else:
                    self.send_message("terminal_user", f"[echo] {user_input}")
        except KeyboardInterrupt:
            print("\n\n[Terminal] 已退出")
