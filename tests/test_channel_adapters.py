from __future__ import annotations

import asyncio
import base64
import struct
from hashlib import sha1
from types import SimpleNamespace

from Crypto.Cipher import AES

from core.systems.integration.channel_manager import ChannelManager
from core.systems.integration.channel_runtime import ChannelConfig, ChannelWebhookRequest
from core.systems.integration.wechat_channel import WeChatOfficialChannel, _sha1_signature
from core.systems.integration.wecom_channel import WeComChannel


def _pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    pad = block_size - (len(data) % block_size)
    return data + bytes([pad]) * pad


def _encrypt_wecom_message(encoding_aes_key: str, receive_id: str, plaintext: str) -> str:
    key = base64.b64decode(f"{encoding_aes_key}=")
    iv = key[:16]
    plain = b"0123456789ABCDEF" + struct.pack(">I", len(plaintext.encode("utf-8"))) + plaintext.encode("utf-8")
    plain += receive_id.encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(_pkcs7_pad(plain))
    return base64.b64encode(encrypted).decode("utf-8")


def _wecom_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    payload = "".join(sorted([token, timestamp, nonce, encrypted]))
    return sha1(payload.encode("utf-8")).hexdigest()


def test_channel_manager_registers_wechat_and_wecom_from_config(temp_paths):
    manager = ChannelManager(
        str(temp_paths.workspace_dir),
        channel_configs={
            "wechat": {"token": "wechat-token"},
            "wecom": {
                "token": "wecom-token",
                "corp_id": "wxcorp",
                "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
            },
        },
    )

    assert {"webhook", "wechat", "wecom"}.issubset(set(manager.list_channels()))


def test_wechat_channel_verifies_get_challenge():
    channel = WeChatOfficialChannel(ChannelConfig(name="wechat", kind="wechat", token="wechat-token"))
    timestamp = "1700000000"
    nonce = "abc123"
    signature = _sha1_signature("wechat-token", timestamp, nonce)
    request = ChannelWebhookRequest(
        method="GET",
        query_params={
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": signature,
            "echostr": "challenge-ok",
        },
    )

    result = channel.verify_webhook(request)

    assert result is not None
    assert result.verified is True
    assert result.challenge == "challenge-ok"


def test_wechat_channel_parses_text_xml_message():
    channel = WeChatOfficialChannel(ChannelConfig(name="wechat", kind="wechat", token="wechat-token"))
    request = ChannelWebhookRequest(
        method="POST",
        raw_body=(
            b"<xml>"
            b"<ToUserName><![CDATA[gh_test]]></ToUserName>"
            b"<FromUserName><![CDATA[user_1]]></FromUserName>"
            b"<MsgType><![CDATA[text]]></MsgType>"
            b"<Content><![CDATA[hello pybot]]></Content>"
            b"<MsgId>123</MsgId>"
            b"</xml>"
        ),
        content_type="application/xml",
    )

    parsed = channel.parse_incoming(request)

    assert parsed is not None
    assert parsed["user_id"] == "user_1"
    assert parsed["message"] == "hello pybot"
    assert parsed["thread_id"] == "wechat:user_1"
    assert parsed["metadata"]["to_user"] == "gh_test"


def test_wechat_channel_passive_reply_returns_xml():
    channel = WeChatOfficialChannel(
        ChannelConfig(name="wechat", kind="wechat", token="wechat-token", app_id="gh_test", reply_mode="passive")
    )
    message_context = channel.parse_incoming(
        ChannelWebhookRequest(
            method="POST",
            raw_body=(
                b"<xml>"
                b"<ToUserName><![CDATA[gh_test]]></ToUserName>"
                b"<FromUserName><![CDATA[user_1]]></FromUserName>"
                b"<MsgType><![CDATA[text]]></MsgType>"
                b"<Content><![CDATA[ping]]></Content>"
                b"</xml>"
            ),
            content_type="application/xml",
        )
    )

    assert message_context is not None
    result = asyncio.run(
        channel.async_send_message(
            "user_1",
            "pong",
            message_context=SimpleNamespace(metadata=message_context["metadata"]),
        )
    )

    assert result.success is True
    assert result.content_type == "application/xml"
    assert "<Content><![CDATA[pong]]></Content>" in (result.response_body or "")


def test_wecom_channel_verifies_get_challenge():
    token = "wecom-token"
    corp_id = "wxcorp"
    encoding_aes_key = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
    channel = WeComChannel(
        ChannelConfig(
            name="wecom",
            kind="wecom",
            token=token,
            corp_id=corp_id,
            encoding_aes_key=encoding_aes_key,
        )
    )
    timestamp = "1700000000"
    nonce = "nonce-1"
    encrypted = _encrypt_wecom_message(encoding_aes_key, corp_id, "challenge-ok")
    request = ChannelWebhookRequest(
        method="GET",
        query_params={
            "timestamp": timestamp,
            "nonce": nonce,
            "msg_signature": _wecom_signature(token, timestamp, nonce, encrypted),
            "echostr": encrypted,
        },
    )

    result = channel.verify_webhook(request)

    assert result is not None
    assert result.verified is True
    assert result.challenge == "challenge-ok"


def test_wecom_channel_parses_encrypted_text_message():
    token = "wecom-token"
    corp_id = "wxcorp"
    encoding_aes_key = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
    channel = WeComChannel(
        ChannelConfig(
            name="wecom",
            kind="wecom",
            token=token,
            corp_id=corp_id,
            encoding_aes_key=encoding_aes_key,
        )
    )
    inner_xml = (
        "<xml>"
        "<ToUserName><![CDATA[wxcorp]]></ToUserName>"
        "<FromUserName><![CDATA[user_2]]></FromUserName>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[hello corp]]></Content>"
        "<AgentID>1000002</AgentID>"
        "<MsgId>456</MsgId>"
        "</xml>"
    )
    encrypted = _encrypt_wecom_message(encoding_aes_key, corp_id, inner_xml)
    outer_xml = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"
    request = ChannelWebhookRequest(
        method="POST",
        query_params={
            "timestamp": "1700000000",
            "nonce": "nonce-2",
            "msg_signature": _wecom_signature(token, "1700000000", "nonce-2", encrypted),
        },
        raw_body=outer_xml.encode("utf-8"),
        content_type="application/xml",
    )

    verification = channel.verify_webhook(request)
    parsed = channel.parse_incoming(request)

    assert verification is not None
    assert verification.verified is True
    assert parsed is not None
    assert parsed["user_id"] == "user_2"
    assert parsed["message"] == "hello corp"
    assert parsed["thread_id"] == "wecom:user_2"
    assert parsed["metadata"]["agent_id"] == "1000002"
