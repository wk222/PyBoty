"""Channel integration system entrypoints."""

from core.systems.integration.channel_manager import BaseChannel, ChannelManager
from core.systems.integration.channel_runtime import ChannelConfig
from core.systems.integration.wechat_channel import WeChatOfficialChannel
from core.systems.integration.wecom_channel import WeComChannel

__all__ = [
    "BaseChannel",
    "ChannelConfig",
    "ChannelManager",
    "WeChatOfficialChannel",
    "WeComChannel",
]
