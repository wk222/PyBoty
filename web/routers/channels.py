"""Channel management API — 渠道管理和微信 ClawBot 登录端点。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.dependencies import get_services
from web.state import WebServices

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/channels", tags=["channels"])
SERVICES_DEPENDENCY = Depends(get_services)


class QrLoginStartResponse(BaseModel):
    qrcode: str
    qrcode_img_url: str
    status: str = "wait"


class QrStatusResponse(BaseModel):
    status: str
    logged_in: bool = False
    user_id: str = ""


class ChannelListItem(BaseModel):
    name: str
    kind: str
    enabled: bool
    dm_policy: str = "open"


class PairingApproveRequest(BaseModel):
    channel: str
    code: str


# ── 渠道列表 ──────────────────────────────────────────────────────────────

@router.get("", response_model=list[ChannelListItem])
def list_channels(services: WebServices = SERVICES_DEPENDENCY):
    """列出所有已注册渠道。"""
    mgr = _get_channel_manager(services)
    if mgr is None:
        return []
    result = []
    for name in mgr.list_channels():
        ch = mgr.get_channel(name)
        if ch:
            kind = getattr(ch.config, "kind", name) if hasattr(ch, "config") else name
            enabled = getattr(ch.config, "enabled", True) if hasattr(ch, "config") else True
            dm_policy = getattr(ch.config, "dm_policy", "open") if hasattr(ch, "config") else "open"
            result.append(ChannelListItem(name=name, kind=kind, enabled=enabled, dm_policy=dm_policy))
    return result


# ── 微信 ClawBot 登录 ─────────────────────────────────────────────────────

@router.get("/wechat_claw/status")
def wechat_claw_status(services: WebServices = SERVICES_DEPENDENCY):
    """检查微信 ClawBot 登录状态。"""
    claw = _get_claw_channel(services)
    if claw is None:
        return {"logged_in": False, "error": "wechat_claw channel not registered"}
    return {"logged_in": claw.is_logged_in, "polling": claw._running}


@router.post("/wechat_claw/login", response_model=QrLoginStartResponse)
def wechat_claw_login_start(services: WebServices = SERVICES_DEPENDENCY):
    """发起微信 ClawBot QR 码登录。返回二维码 URL。"""
    claw = _get_claw_channel(services)
    if claw is None:
        raise HTTPException(404, "wechat_claw channel not registered")

    existing = claw.login(force=False)
    if existing:
        return QrLoginStartResponse(
            qrcode="",
            qrcode_img_url="",
            status="already_logged_in",
        )

    try:
        qr_data = claw.fetch_qr_code()
    except Exception as exc:
        logger.error("[WeChatClaw] QR fetch error: %s", exc)
        raise HTTPException(502, f"Failed to fetch QR code: {exc}")

    return QrLoginStartResponse(
        qrcode=qr_data.get("qrcode", ""),
        qrcode_img_url=qr_data.get("qrcode_img_content", ""),
        status="wait",
    )


@router.get("/wechat_claw/login/poll")
def wechat_claw_login_poll(
    qrcode: str,
    services: WebServices = SERVICES_DEPENDENCY,
):
    """轮询二维码扫描状态。

    Query params:
      qrcode — 从 /login 接口拿到的 qrcode 值

    返回 status: wait / scaned / confirmed / expired
    """
    claw = _get_claw_channel(services)
    if claw is None:
        raise HTTPException(404, "wechat_claw channel not registered")

    try:
        status_data = claw.poll_qr_status(qrcode)
    except Exception as exc:
        logger.error("[WeChatClaw] QR poll error: %s", exc)
        raise HTTPException(502, f"Poll failed: {exc}")

    result: dict[str, Any] = {"status": status_data.get("status", "unknown")}

    if status_data.get("status") == "confirmed":
        creds = claw.confirm_login(status_data)
        if creds:
            result["logged_in"] = True
            result["user_id"] = creds.user_id
            _try_start_polling(claw, services)
        else:
            result["logged_in"] = False
            result["error"] = "Login confirmed but credentials invalid"

    return result


@router.post("/wechat_claw/logout")
def wechat_claw_logout(services: WebServices = SERVICES_DEPENDENCY):
    """登出微信 ClawBot 并清除凭证。"""
    claw = _get_claw_channel(services)
    if claw is None:
        raise HTTPException(404, "wechat_claw channel not registered")
    claw.stop_polling()
    claw._clear_credentials()
    return {"success": True}


@router.post("/wechat_claw/start_polling")
def wechat_claw_start_polling(services: WebServices = SERVICES_DEPENDENCY):
    """手动启动消息轮询。"""
    claw = _get_claw_channel(services)
    if claw is None:
        raise HTTPException(404, "wechat_claw channel not registered")
    if not claw.is_logged_in:
        raise HTTPException(400, "Not logged in")
    _try_start_polling(claw, services)
    return {"success": True, "polling": True}


@router.get("/pairing/pending")
def list_channel_pairings(services: WebServices = SERVICES_DEPENDENCY):
    """List pending IM pairing codes (OpenClaw dmPolicy=pairing)."""
    mgr = _get_channel_manager(services)
    if mgr is None:
        return {"pending": []}
    runtime = getattr(mgr, "_runtime", None)
    if runtime is None or not hasattr(runtime, "list_pairings"):
        return {"pending": []}
    return {"pending": runtime.list_pairings()}


@router.post("/pairing/approve")
def approve_channel_pairing(
    req: PairingApproveRequest,
    services: WebServices = SERVICES_DEPENDENCY,
):
    mgr = _get_channel_manager(services)
    if mgr is None:
        raise HTTPException(404, "channel manager not available")
    runtime = getattr(mgr, "_runtime", None)
    if runtime is None or not hasattr(runtime, "approve_pairing"):
        raise HTTPException(503, "pairing runtime unavailable")
    user_id = runtime.approve_pairing(req.channel, req.code)
    if not user_id:
        raise HTTPException(400, "Invalid pairing code")
    return {"success": True, "channel": req.channel, "user_id": user_id}


# ── 内部工具 ──────────────────────────────────────────────────────────────

def _get_channel_manager(services: WebServices) -> Any:
    try:
        system_agent = services.system_agent()
    except Exception:
        return None
    return getattr(system_agent, "channel_manager", None)


def _get_claw_channel(services: WebServices) -> Any:
    mgr = _get_channel_manager(services)
    if mgr is None:
        return None
    return mgr.get_channel("wechat_claw")


def _try_start_polling(claw: Any, services: WebServices) -> None:
    """尝试启动长轮询，注入 agent 回调。"""
    mgr = _get_channel_manager(services)
    if mgr is None:
        claw.start_polling()
        return

    runtime = getattr(mgr, "_runtime", None)
    if runtime and hasattr(runtime, "_agent_callback") and runtime._agent_callback:
        claw.start_polling(agent_callback=runtime._agent_callback)
    else:
        claw.start_polling()
