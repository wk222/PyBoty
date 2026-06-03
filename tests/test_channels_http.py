"""HTTP integration tests for /api/channels routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest


@dataclass
class FakeClawChannel:
    is_logged_in: bool = False
    _running: bool = False
    login_calls: list[bool] = field(default_factory=list)
    poll_calls: list[str] = field(default_factory=list)
    started_polling: bool = False
    cleared_credentials: bool = False
    stopped_polling: bool = False

    def login(self, force: bool = False):
        self.login_calls.append(force)
        if self.is_logged_in:
            return {"status": "already_logged_in"}
        return None

    def fetch_qr_code(self):
        return {"qrcode": "qr-token-123", "qrcode_img_content": "data:image/png;base64,abc"}

    def poll_qr_status(self, qrcode: str):
        self.poll_calls.append(qrcode)
        if qrcode == "expired-token":
            return {"status": "expired"}
        return {"status": "confirmed", "user_id": "wx-user-1"}

    def confirm_login(self, status_data: dict[str, Any]):
        if status_data.get("status") != "confirmed":
            return None
        self.is_logged_in = True
        return SimpleNamespace(user_id=status_data.get("user_id", "wx-user-1"))

    def stop_polling(self):
        self.stopped_polling = True
        self._running = False

    def _clear_credentials(self):
        self.cleared_credentials = True
        self.is_logged_in = False

    def start_polling(self, agent_callback=None):
        self.started_polling = True
        self._running = True


class FakeChannelManager:
    def __init__(self, channels: dict[str, Any] | None = None):
        self._channels = channels or {}
        self._runtime = SimpleNamespace(_agent_callback=lambda *_a, **_k: "agent-reply")

    def list_channels(self):
        return list(self._channels.keys())

    def get_channel(self, name: str):
        return self._channels.get(name)


class FakeSystemAgent:
    def __init__(self, channel_manager: FakeChannelManager | None):
        self.channel_manager = channel_manager


def _patch_system_agent(client, monkeypatch, channel_manager: FakeChannelManager | None):
    monkeypatch.setattr(
        client.app.state.services,
        "system_agent",
        lambda: FakeSystemAgent(channel_manager),
    )


def _wechat_config(enabled: bool = True):
    return SimpleNamespace(kind="wechat_claw", enabled=enabled)


class TestChannelsHttpList:
    def test_list_channels_returns_empty_without_manager(self, client, monkeypatch):
        _patch_system_agent(client, monkeypatch, None)
        response = client.get("/api/channels")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_channels_returns_registered_channels(self, client, monkeypatch):
        claw = FakeClawChannel()
        claw.config = _wechat_config(True)
        manager = FakeChannelManager({"wechat_claw": claw, "webhook": SimpleNamespace(config=_wechat_config(False))})
        _patch_system_agent(client, monkeypatch, manager)

        response = client.get("/api/channels")
        assert response.status_code == 200
        payload = response.json()
        assert {item["name"] for item in payload} == {"wechat_claw", "webhook"}
        by_name = {item["name"]: item for item in payload}
        assert by_name["wechat_claw"]["kind"] == "wechat_claw"
        assert by_name["wechat_claw"]["enabled"] is True
        assert by_name["webhook"]["enabled"] is False


class TestWechatClawHttp:
    @pytest.fixture
    def claw_setup(self, client, monkeypatch):
        claw = FakeClawChannel()
        claw.config = _wechat_config(True)
        manager = FakeChannelManager({"wechat_claw": claw})
        _patch_system_agent(client, monkeypatch, manager)
        return claw

    def test_status_without_channel_reports_not_registered(self, client, monkeypatch):
        _patch_system_agent(client, monkeypatch, FakeChannelManager({}))
        response = client.get("/api/channels/wechat_claw/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["logged_in"] is False
        assert "not registered" in payload["error"]

    def test_status_reports_login_and_polling(self, client, claw_setup):
        claw_setup.is_logged_in = True
        claw_setup._running = True
        response = client.get("/api/channels/wechat_claw/status")
        assert response.status_code == 200
        assert response.json() == {"logged_in": True, "polling": True}

    def test_login_start_returns_qr_payload(self, client, claw_setup):
        response = client.post("/api/channels/wechat_claw/login")
        assert response.status_code == 200
        payload = response.json()
        assert payload["qrcode"] == "qr-token-123"
        assert payload["qrcode_img_url"].startswith("data:image/png")
        assert payload["status"] == "wait"

    def test_login_start_when_already_logged_in(self, client, claw_setup):
        claw_setup.is_logged_in = True
        response = client.post("/api/channels/wechat_claw/login")
        assert response.status_code == 200
        assert response.json()["status"] == "already_logged_in"

    def test_login_poll_confirms_and_starts_polling(self, client, claw_setup):
        response = client.get("/api/channels/wechat_claw/login/poll", params={"qrcode": "qr-token-123"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "confirmed"
        assert payload["logged_in"] is True
        assert payload["user_id"] == "wx-user-1"
        assert claw_setup.is_logged_in is True
        assert claw_setup.started_polling is True

    def test_login_poll_expired_status(self, client, claw_setup):
        response = client.get("/api/channels/wechat_claw/login/poll", params={"qrcode": "expired-token"})
        assert response.status_code == 200
        assert response.json()["status"] == "expired"

    def test_logout_clears_credentials(self, client, claw_setup):
        claw_setup.is_logged_in = True
        claw_setup._running = True
        response = client.post("/api/channels/wechat_claw/logout")
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert claw_setup.cleared_credentials is True
        assert claw_setup.stopped_polling is True
        assert claw_setup.is_logged_in is False

    def test_start_polling_requires_login(self, client, claw_setup):
        response = client.post("/api/channels/wechat_claw/start_polling")
        assert response.status_code == 400
        assert "Not logged in" in response.json()["detail"]

    def test_start_polling_when_logged_in(self, client, claw_setup):
        claw_setup.is_logged_in = True
        response = client.post("/api/channels/wechat_claw/start_polling")
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["polling"] is True
        assert claw_setup.started_polling is True

    def test_wechat_claw_routes_404_without_channel(self, client, monkeypatch):
        _patch_system_agent(client, monkeypatch, FakeChannelManager({}))
        assert client.post("/api/channels/wechat_claw/login").status_code == 404
        assert client.post("/api/channels/wechat_claw/logout").status_code == 404
