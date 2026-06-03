"""Channel integration subpackage: webhook + IM channel adapters."""

from core.systems.integration.channels.channel_manager import BaseChannel, ChannelManager
from core.systems.integration.channels.channel_runtime import (
    ChannelConfig,
    ChannelMessage,
    ChannelRouteDecision,
    ChannelWebhookRequest,
    ChannelWebhookVerification,
)
from core.systems.integration.channels.dingtalk_channel import DingTalkChannel
from core.systems.integration.channels.feishu_channel import FeishuChannel
from core.systems.integration.channels.terminal_channel import TerminalChannel
from core.systems.integration.channels.wechat_channel import WeChatOfficialChannel
from core.systems.integration.channels.wechat_claw_channel import WeChatClawChannel
from core.systems.integration.channels.wecom_channel import WeComChannel

__all__ = [
    "BaseChannel",
    "ChannelConfig",
    "ChannelManager",
    "ChannelMessage",
    "ChannelRouteDecision",
    "ChannelWebhookRequest",
    "ChannelWebhookVerification",
    "DingTalkChannel",
    "FeishuChannel",
    "TerminalChannel",
    "WeChatClawChannel",
    "WeChatOfficialChannel",
    "WeComChannel",
]
