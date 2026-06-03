"""Gateway compatibility APIs inspired by OpenClaw/OpenResponses."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from secrets import token_hex
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.systems.integration import GatewayPresenceEntry
from core.systems.integration.channels.channel_runtime import ChannelMessage, ChannelRouteDecision
from core.systems.integration.openresponses import (
    OpenResponsesRequest,
    PreparedGatewayRequest,
    build_gateway_models_catalog,
    build_openresponses_created_event,
    build_openresponses_payload,
    build_openresponses_tool_call_payload,
    parse_bearer_token,
    prepare_gateway_request,
    request_has_function_call_output,
    select_client_tool_name,
)
from core.systems.runtime import get_gateway_config
from web.dependencies import get_services
from web.state import WebServices

logger = logging.getLogger(__name__)
router = APIRouter(tags=["gateway"])
SERVICES_DEPENDENCY = Depends(get_services)
_WS_PROTOCOL_VERSION = 3
_GATEWAY_WS_CONNECTIONS_LOCK = threading.RLock()


@dataclass
class _GatewayWsClient:
    connection_id: str
    websocket: WebSocket
    role: str
    scopes: tuple[str, ...]
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def is_operator(self) -> bool:
        return self.role == "operator" or any(scope.startswith("operator") for scope in self.scopes)


_GATEWAY_WS_CONNECTIONS: dict[str, _GatewayWsClient] = {}


class ChannelRoutePreviewRequest(BaseModel):
    channel_name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class GatewayRouteChatRequest(BaseModel):
    channel_name: str
    message: str
    user_id: str
    thread_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewaySessionInjectRequest(BaseModel):
    message: str = ""
    role: str = "system"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    mode: str = "assistant"


class GatewayAbortRequest(BaseModel):
    run_id: str | None = None
    note: str = ""
    requested_by: str = "operator"


class GatewayNodeInvokeRequest(BaseModel):
    command: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayNodeAckRequest(BaseModel):
    status: str = "completed"
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


def _gateway_endpoint_enabled(name: str) -> bool:
    gateway_config = get_gateway_config()
    http_cfg = gateway_config.get("http", {})
    endpoints = http_cfg.get("endpoints", {})
    endpoint_cfg = endpoints.get(name, {})
    return bool(endpoint_cfg.get("enabled", False))


def _require_gateway_auth(request: Request) -> dict[str, Any]:
    gateway_config = get_gateway_config()
    auth_cfg = gateway_config.get("auth", {})
    auth_mode = str(auth_cfg.get("mode", "none")).strip().lower() or "none"
    expected_secret = ""
    if auth_mode == "token":
        expected_secret = str(auth_cfg.get("token") or "").strip()
    elif auth_mode == "password":
        expected_secret = str(auth_cfg.get("password") or "").strip()

    if auth_mode == "none" or not expected_secret:
        return gateway_config

    provided = parse_bearer_token(request.headers.get("authorization"))
    if provided != expected_secret:
        raise HTTPException(
            status_code=401,
            detail="Gateway authorization failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return gateway_config


def _require_gateway_agent(
    services: WebServices,
    *,
    root_mode: str,
    thread_id: str,
) -> Any:
    try:
        return services.agents.get_or_create_mode(root_mode, thread_id)
    except Exception as exc:
        msg = str(exc)
        if "api_key" in msg.lower() or "OPENAI_API_KEY" in msg:
            raise HTTPException(
                status_code=503,
                detail=(
                    "LLM not configured. Set the OPENAI_API_KEY environment variable "
                    "or run `pybot-onboard` to configure your API key."
                ),
            ) from exc
        logger.exception("Failed to create gateway agent for mode %s", root_mode)
        raise HTTPException(status_code=500, detail=msg) from exc


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _supported_channels(services: WebServices) -> list[str]:
    if not services.llm_configured:
        return []
    try:
        system_agent = services.system_agent()
    except Exception:
        return []
    channel_manager = getattr(system_agent, "channel_manager", None)
    if channel_manager is None or not hasattr(channel_manager, "list_channels"):
        return []
    channels = channel_manager.list_channels()
    if isinstance(channels, dict):
        return sorted(channels.keys())
    return sorted(str(item) for item in channels)


def _system_channel_manager(services: WebServices) -> Any | None:
    if not services.llm_configured:
        return None
    try:
        system_agent = services.system_agent()
    except Exception:
        return None
    channel_manager = getattr(system_agent, "channel_manager", None)
    return channel_manager if channel_manager is not None else None


def _gateway_ws_config() -> dict[str, Any]:
    return get_gateway_config().get("ws", {})


def _gateway_pairing_config() -> dict[str, Any]:
    return get_gateway_config().get("pairing", {})


def _redact_channel_config(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    payload = {
        "name": getattr(config, "name", ""),
        "kind": getattr(config, "kind", ""),
        "enabled": getattr(config, "enabled", True),
        "reply_mode": getattr(config, "reply_mode", ""),
        "has_token": bool(getattr(config, "token", None)),
        "has_app_id": bool(getattr(config, "app_id", None)),
        "has_app_secret": bool(getattr(config, "app_secret", None)),
        "has_corp_id": bool(getattr(config, "corp_id", None)),
        "has_agent_id": bool(getattr(config, "agent_id", None)),
        "has_secret": bool(getattr(config, "secret", None)),
        "has_encoding_aes_key": bool(getattr(config, "encoding_aes_key", None)),
        "api_base": getattr(config, "api_base", None),
        "extra_keys": sorted(getattr(config, "extra", {}).keys()) if hasattr(config, "extra") else [],
    }
    return payload


def _ws_response(*, request_id: str, ok: bool, payload: Any | None = None, error: Any | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"type": "res", "id": request_id, "ok": ok}
    if ok:
        body["payload"] = payload
    else:
        body["error"] = error or {"message": "request failed"}
    return body


def _is_local_client(host: str | None) -> bool:
    normalized = (host or "").strip().lower()
    return normalized in {"127.0.0.1", "::1", "localhost", "testclient"}


def _sync_session_runtime(services: WebServices) -> None:
    services.sync_session_spine()


def _gateway_session_items(services: WebServices) -> list[dict[str, Any]]:
    _sync_session_runtime(services)
    items: list[dict[str, Any]] = []
    for item in services.session_runtime.list_sessions():
        thread_id = str(item.get("thread_id", "")).strip()
        sources = [str(source).strip() for source in item.get("source_history", [])]
        is_gateway_session = thread_id.startswith("gateway-") or any(
            "gateway" in source or source.startswith("ws.") for source in sources
        )
        if not is_gateway_session:
            continue
        gateway_meta = item.get("gateway", {}) if isinstance(item.get("gateway"), dict) else {}
        items.append(
            {
                **item,
                "mode": item.get("primary_mode", "assistant"),
                "user": gateway_meta.get("user", ""),
                "device_ids": gateway_meta.get("device_ids", []),
                "client_ids": gateway_meta.get("client_ids", []),
                "active_connections": len(services.gateway_runtime.presence.by_session(item["session_key"])),
            }
        )
    return items


def _gateway_runs_snapshot(services: WebServices) -> dict[str, Any]:
    return {
        "items": services.gateway_runtime.runs.list(),
        "active": services.gateway_runtime.runs.list_active(),
    }


def _gateway_node_commands_snapshot(services: WebServices, *, device_id: str | None = None) -> dict[str, Any]:
    items = services.gateway_runtime.node_commands.list(device_id=device_id)
    pending = services.gateway_runtime.node_commands.pending_for_device(device_id) if device_id else []
    return {
        "items": items,
        "pending": pending,
    }


def _declared_node_commands(services: WebServices, device_id: str) -> list[str]:
    node = services.gateway_runtime.nodes.get(device_id)
    if node is None:
        return []
    metadata = getattr(node, "metadata", {}) or {}
    commands = metadata.get("commands", [])
    if not isinstance(commands, list):
        return []
    return [str(item).strip() for item in commands if str(item).strip()]


def _prepared_from_run_record(record: Any) -> PreparedGatewayRequest:
    return PreparedGatewayRequest(
        response_id=str(record.response_id),
        requested_model=str(record.requested_model or f"pybot:{record.mode}"),
        resolved_mode=str(record.mode or "assistant"),
        session_key=str(record.session_key),
        thread_id=str(record.thread_id),
        prompt=str(record.display_input),
        display_input=str(record.display_input or "(empty input)"),
        ignored_features=list(getattr(record, "ignored_features", [])),
        client_tools=list(getattr(record, "client_tools", [])),
        metadata=dict(getattr(record, "metadata", {}) or {}),
    )


def _payload_from_run_record(record: Any) -> dict[str, Any]:
    if isinstance(getattr(record, "response_payload", None), dict):
        return dict(record.response_payload)
    prepared = _prepared_from_run_record(record)
    output_text = str(getattr(record, "output_text", "") or "")
    payload = build_openresponses_payload(
        prepared=prepared,
        output_text=output_text,
        status=str(getattr(record, "status", "completed") or "completed"),
    )
    if record.status in {"cancelled", "failed"}:
        payload["output"] = []
        payload["output_text"] = output_text
    if getattr(record, "error", ""):
        payload["error"] = {"message": str(record.error)}
    payload["pybot"]["abort_requested"] = bool(getattr(record, "abort_requested", False))
    payload["pybot"]["abort_note"] = str(getattr(record, "abort_note", ""))
    payload["pybot"]["aborted_by"] = str(getattr(record, "aborted_by", ""))
    return payload


def _resolve_gateway_request_context(
    services: WebServices,
    *,
    payload: OpenResponsesRequest,
    session_key: str | None = None,
    header_agent_id: str | None = None,
) -> PreparedGatewayRequest:
    previous_run = None
    resolved_session_key = session_key
    resolved_agent_id = header_agent_id
    if not resolved_session_key and payload.previous_response_id:
        previous_run = services.gateway_runtime.runs.get_by_response(payload.previous_response_id)
        if previous_run is not None:
            resolved_session_key = previous_run.session_key
            if not resolved_agent_id and payload.model == "pybot:assistant":
                resolved_agent_id = f"pybot:{previous_run.mode}"
    prepared = prepare_gateway_request(
        payload,
        session_key=resolved_session_key,
        header_agent_id=resolved_agent_id,
    )
    if previous_run is not None and "previous_response_id" in prepared.ignored_features:
        prepared.ignored_features = [item for item in prepared.ignored_features if item != "previous_response_id"]
        prepared.metadata = {
            **prepared.metadata,
            "continued_from_response_id": previous_run.response_id,
        }
    return prepared


def _cancelled_run_payload(record: Any, *, note: str = "") -> dict[str, Any]:
    prepared = _prepared_from_run_record(record)
    payload = build_openresponses_payload(prepared=prepared, output_text="", status="cancelled")
    payload["output"] = []
    payload["output_text"] = ""
    payload["error"] = {"message": note or "Run cancelled by operator"}
    payload["pybot"]["abort_requested"] = True
    payload["pybot"]["abort_note"] = note or str(getattr(record, "abort_note", ""))
    payload["pybot"]["aborted_by"] = str(getattr(record, "aborted_by", ""))
    return payload


def _inject_session_messages(
    services: WebServices,
    *,
    prepared: PreparedGatewayRequest,
    items: list[dict[str, Any]],
) -> int:
    injected = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "system")).strip() or "system"
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        services.conversations.ensure_conversation(prepared.thread_id, title_hint=content)
        services.conversations.append_message(prepared.thread_id, role, content)
        services.session_runtime.record_message(
            thread_id=prepared.thread_id,
            role=role,
            content=content,
            root_mode=prepared.resolved_mode,
            source="gateway.inject",
            session_key=prepared.session_key,
        )
        injected += 1
    return injected


def _stringify_channel_route_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("response", "output_text", "result", "message"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _channel_route_callback(
    services: WebServices,
    channel_name: str,
    message: ChannelMessage,
    decision: ChannelRouteDecision,
) -> str:
    # Check if this message is an approval response (e.g., "pybot approve appr_123")
    text = message.message.strip()
    match = re.match(r"(?i)^pybot\s+(approve|reject)\s+(appr_[a-f0-9]+)$", text)
    if match:
        action, approval_id = match.group(1).lower(), match.group(2)
        queue = services.approval_queue
        req = queue.get_request(approval_id)
        if req is not None:
            if req.status != "pending":
                return f"⚠️ 审批请求 {approval_id} 已经处理过了（当前状态: {req.status}）。"
            res = queue.resolve(approval_id, approved=(action == "approve"), resolved_by=f"channel:{channel_name}:{message.user_id}")
            if res.get("success"):
                return f"✅ 成功将审批请求 {approval_id} 标记为 {action.upper()}。"
            return f"❌ 处理审批失败: {res.get('error')}"
        return f"⚠️ 未找到待处理的审批请求: {approval_id}"

    if decision.target == "workflow" and decision.workflow_name:
        engine = services.system_agent().pyflow_engine
        workflow = engine.load_workflow(decision.workflow_name)
        if workflow is None:
            raise RuntimeError(f"workflow not found: {decision.workflow_name}")
        payload = {
            "channel": channel_name,
            "user_id": message.user_id,
            "message": message.message,
            "thread_id": decision.thread_id or message.thread_id,
            "metadata": message.metadata,
            "route": decision.to_dict(),
        }
        workflow.variables.update(payload)
        workflow.variables["input"] = payload
        workflow.variables["channel_message"] = payload
        result = engine.run_workflow(workflow)
        return _stringify_channel_route_result(result)

    thread_id = decision.thread_id or message.thread_id
    mode = decision.mode or "assistant"
    
    # Intercept high-risk tools and inject a callback to notify the channel
    from core.systems.governance.approval_callback import GovernanceApprovalCallback
    agent = services.agents.get_or_create_mode(mode, thread_id)
    
    # Setup channel push callback when a tool requires approval
    def push_approval_to_channel(approved: bool, note: str) -> None:
        mgr = services.system_agent().channel_manager
        ch = mgr.get_channel(channel_name)
        if ch:
            status_str = "已批准" if approved else "已拒绝"
            mgr._runtime._normalize_send_result(
                ch.send_message(message.user_id, f"🔔 审批结果通知: 您发起的工具执行请求已被 {status_str}。备注: {note or '无'}")
            )

    # We hook into the agent's LLM callback to notify the user of pending approvals in real-time
    original_create_request = services.approval_queue.create_request
    def create_request_with_channel_push(*args, **kwargs):
        # Inject our channel notification callback
        kwargs["callback"] = push_approval_to_channel
        req = original_create_request(*args, **kwargs)
        # Send a direct message to the user with approval instructions
        mgr = services.system_agent().channel_manager
        ch = mgr.get_channel(channel_name)
        if ch:
            mgr._runtime._normalize_send_result(
                ch.send_message(
                    message.user_id, 
                    f"⚠️ 治理中心拦截: 智能体即将执行高危工具调用。\n"
                    f"摘要: {req.summary}\n"
                    f"审批 ID: `{req.approval_id}`\n\n"
                    f"请回复 `pybot approve {req.approval_id}` 批准，或 `pybot reject {req.approval_id}` 拒绝。"
                )
            )
        return req

    # Temporarily patch the queue's create_request during this chat turn
    services.approval_queue.create_request = create_request_with_channel_push
    try:
        response = str(agent.chat(message.message))
    finally:
        services.approval_queue.create_request = original_create_request
        
    return response


def _ensure_gateway_channel_routing(services: WebServices) -> Any | None:
    manager = _system_channel_manager(services)
    if manager is None or not hasattr(manager, "set_route_callback"):
        return manager
    manager.set_route_callback(
        lambda channel_name, message, decision: _channel_route_callback(services, channel_name, message, decision)
    )
    return manager


def _gateway_tool_call_arguments(
    *,
    prepared: Any,
    payload: OpenResponsesRequest,
) -> dict[str, Any]:
    return {
        "input": prepared.display_input,
        "mode": prepared.resolved_mode,
        "session_key": prepared.session_key,
        "metadata": payload.metadata,
    }


def _require_gateway_ws_auth(params: dict[str, Any]) -> dict[str, Any]:
    gateway_config = get_gateway_config()
    auth_cfg = gateway_config.get("auth", {})
    auth_mode = str(auth_cfg.get("mode", "none")).strip().lower() or "none"
    if auth_mode == "none":
        return gateway_config

    auth = params.get("auth", {})
    token = str(auth.get("token", "")).strip() if isinstance(auth, dict) else ""
    expected = ""
    if auth_mode == "token":
        expected = str(auth_cfg.get("token") or "").strip()
    elif auth_mode == "password":
        expected = str(auth_cfg.get("password") or "").strip()
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="Gateway authorization failed")
    return gateway_config


def _build_presence_entry(
    *,
    connection_id: str,
    params: dict[str, Any],
    session_key: str,
) -> GatewayPresenceEntry:
    client = params.get("client", {}) if isinstance(params.get("client"), dict) else {}
    device = params.get("device", {}) if isinstance(params.get("device"), dict) else {}
    return GatewayPresenceEntry(
        connection_id=connection_id,
        device_id=str(device.get("id", "")).strip(),
        role=str(params.get("role", "operator")).strip() or "operator",
        scopes=[str(item) for item in params.get("scopes", []) if str(item).strip()],
        client_id=str(client.get("id", "")).strip(),
        client_version=str(client.get("version", "")).strip(),
        platform=str(client.get("platform", "")).strip(),
        mode=str(client.get("mode", "")).strip(),
        session_key=session_key,
        user_agent=str(params.get("userAgent", "")).strip(),
        metadata={"commands": params.get("commands", []), "caps": params.get("caps", [])},
    )


def _extract_gateway_device_token(params: dict[str, Any]) -> str:
    auth = params.get("auth", {}) if isinstance(params.get("auth"), dict) else {}
    device = params.get("device", {}) if isinstance(params.get("device"), dict) else {}
    return (
        str(auth.get("device_token", "")).strip()
        or str(device.get("token", "")).strip()
        or str(params.get("deviceToken", "")).strip()
    )


def _register_gateway_ws_client(client: _GatewayWsClient) -> None:
    with _GATEWAY_WS_CONNECTIONS_LOCK:
        _GATEWAY_WS_CONNECTIONS[client.connection_id] = client


def _unregister_gateway_ws_client(connection_id: str) -> None:
    with _GATEWAY_WS_CONNECTIONS_LOCK:
        _GATEWAY_WS_CONNECTIONS.pop(connection_id, None)


def _gateway_ws_clients_snapshot() -> list[_GatewayWsClient]:
    with _GATEWAY_WS_CONNECTIONS_LOCK:
        return list(_GATEWAY_WS_CONNECTIONS.values())


async def _send_gateway_frame(client: _GatewayWsClient, frame: dict[str, Any]) -> None:
    async with client.send_lock:
        await client.websocket.send_json(frame)


async def _broadcast_gateway_event(
    event: str,
    payload: dict[str, Any],
    *,
    operator_only: bool = False,
    exclude_connection_ids: set[str] | None = None,
) -> None:
    stale_connections: list[str] = []
    excluded = exclude_connection_ids or set()
    for client in _gateway_ws_clients_snapshot():
        if client.connection_id in excluded:
            continue
        if operator_only and not client.is_operator:
            continue
        try:
            await _send_gateway_frame(
                client,
                {
                    "type": "event",
                    "event": event,
                    "payload": payload,
                },
            )
        except Exception:
            stale_connections.append(client.connection_id)
    for connection_id in stale_connections:
        _unregister_gateway_ws_client(connection_id)


def _gateway_presence_snapshot(services: WebServices) -> dict[str, Any]:
    items = services.gateway_runtime.presence.list()
    return {"items": items, "presence": items}


def _gateway_pairing_snapshot(services: WebServices) -> dict[str, Any]:
    return {
        "pending": services.gateway_runtime.pairings.list_pending(),
        "approved": services.gateway_runtime.pairings.list_approved(),
    }


def _gateway_operator_methods() -> list[str]:
    return [
        "system.presence",
        "system-presence",
        "nodes.list",
        "nodes.get",
        "node.invoke",
        "sessions.get",
        "runs.list",
        "runs.get",
        "runs.abort",
        "device.pair.list",
        "device.pair.approve",
        "device.pair.reject",
        "channel-routes.list",
        "channel-routes.preview",
    ]


def _gateway_features(services: WebServices) -> dict[str, Any]:
    return {
        "events": [
            "tick",
            "presence",
            "device.pair.requested",
            "device.pair.resolved",
            "run.updated",
            "node.command.updated",
        ],
        "operatorMethods": _gateway_operator_methods(),
        "methods": _gateway_operator_methods()
        + [
            "ping",
            "approvals.list",
            "exec.approval.resolve",
            "channels.list",
            "sessions.list",
            "sessions.get",
            "chat.history",
            "chat.inject",
            "chat.abort",
            "node.pending.pull",
            "node.pending.ack",
            "tools.catalog",
            "chat.send",
        ],
        "channels": _supported_channels(services),
    }


def _resolve_pairing_identifier(params: dict[str, Any]) -> str:
    return str(params.get("request_id", "")).strip() or str(params.get("device_id", "")).strip()


@router.get("/v1/models")
async def list_gateway_models(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,  # noqa: ARG001 - keeps dependency symmetry
) -> dict[str, Any]:
    _require_gateway_auth(request)
    if not _gateway_endpoint_enabled("models"):
        raise HTTPException(status_code=404, detail="Gateway models endpoint is disabled")
    return {"object": "list", "data": build_gateway_models_catalog()}


@router.get("/api/gateway/status")
async def get_gateway_status(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    gateway_config = _require_gateway_auth(request)
    responses_cfg = gateway_config.get("http", {}).get("endpoints", {}).get("responses", {})
    return {
        "status": "ok",
        "responses_enabled": bool(responses_cfg.get("enabled", False)),
        "stream_enabled": bool(responses_cfg.get("stream_enabled", False)),
        "ws_enabled": bool(_gateway_ws_config().get("enabled", True)),
        "auth_mode": str(gateway_config.get("auth", {}).get("mode", "none")),
        "supported_models": [item["id"] for item in build_gateway_models_catalog()],
        "supported_channels": _supported_channels(services),
        "presence_count": len(services.gateway_runtime.presence.list()),
        "pending_pairings": len(services.gateway_runtime.pairings.list_pending()),
        "session_count": len(services.gateway_runtime.sessions.list()),
        "active_run_count": len(services.gateway_runtime.runs.list_active()),
        "run_count": len(services.gateway_runtime.runs.list()),
    }


@router.get("/api/gateway/sessions")
async def list_gateway_sessions(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    return {"sessions": _gateway_session_items(services)}


@router.get("/api/gateway/sessions/{session_key}")
async def get_gateway_session_detail(
    session_key: str,
    request: Request,
    mode: str = "assistant",
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    _sync_session_runtime(services)
    prepared = prepare_gateway_request(
        OpenResponsesRequest(model=f"pybot:{mode}", input="session lookup"),
        session_key=session_key,
    )
    history = services.conversations.get_history(prepared.thread_id)
    session_meta = services.gateway_runtime.sessions.get(prepared.session_key)
    presence = services.gateway_runtime.presence.by_session(prepared.session_key)
    latest_run = services.gateway_runtime.runs.latest_for_session(prepared.session_key)
    return {
        "thread_id": prepared.thread_id,
        "mode": prepared.resolved_mode,
        "session_key": prepared.session_key,
        "session": services.session_runtime.get_session(prepared.session_key),
        "session_meta": session_meta.to_dict() if session_meta is not None else None,
        "presence": presence,
        "history": history,
        "latest_run": latest_run.to_dict() if latest_run is not None else None,
    }


@router.post("/api/gateway/sessions/{session_key}/inject")
async def inject_gateway_session_messages(
    session_key: str,
    payload: GatewaySessionInjectRequest,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    prepared = prepare_gateway_request(
        OpenResponsesRequest(model=f"pybot:{payload.mode}", input="inject"),
        session_key=session_key,
    )
    items = payload.messages
    if not items:
        message = payload.message.strip()
        items = [{"role": payload.role, "content": message}] if message else []
    injected = _inject_session_messages(services, prepared=prepared, items=items)
    return {
        "thread_id": prepared.thread_id,
        "mode": prepared.resolved_mode,
        "session_key": prepared.session_key,
        "injected": injected,
    }


@router.post("/api/gateway/sessions/{session_key}/abort")
async def abort_gateway_session_run(
    session_key: str,
    payload: GatewayAbortRequest,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    record = services.gateway_runtime.runs.request_abort_for_session(
        session_key,
        note=payload.note,
        requested_by=payload.requested_by,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="No active gateway run found for session")
    await _broadcast_gateway_event("run.updated", {"run": record.to_dict()}, operator_only=True)
    return {"success": True, "run": record.to_dict()}


@router.get("/api/gateway/runs")
async def list_gateway_runs(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    return _gateway_runs_snapshot(services)


@router.get("/api/gateway/runs/{run_id}")
async def get_gateway_run_detail(
    run_id: str,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    record = services.gateway_runtime.runs.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Gateway run not found")
    return {"run": record.to_dict(), "response": _payload_from_run_record(record)}


@router.post("/api/gateway/runs/{run_id}/abort")
async def abort_gateway_run(
    run_id: str,
    payload: GatewayAbortRequest,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    record = services.gateway_runtime.runs.request_abort(
        run_id,
        note=payload.note,
        requested_by=payload.requested_by,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Gateway run not found")
    await _broadcast_gateway_event("run.updated", {"run": record.to_dict()}, operator_only=True)
    return {"success": True, "run": record.to_dict()}


@router.get("/api/gateway/nodes")
async def list_gateway_nodes(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    return {"nodes": services.gateway_runtime.nodes.list()}


@router.get("/api/gateway/nodes/{device_id}")
async def get_gateway_node_detail(
    device_id: str,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    node = services.gateway_runtime.nodes.get(device_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Gateway node not found")
    return {
        "node": node.to_dict(),
        "presence": services.gateway_runtime.presence.by_device(device_id),
        "commands": _gateway_node_commands_snapshot(services, device_id=device_id),
        "pairing": services.gateway_runtime.pairings.get_request(device_id).to_dict()
        if services.gateway_runtime.pairings.get_request(device_id) is not None
        else None,
    }


@router.post("/api/gateway/nodes/{device_id}/invoke")
async def invoke_gateway_node(
    device_id: str,
    payload: GatewayNodeInvokeRequest,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    node = services.gateway_runtime.nodes.get(device_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Gateway node not found")
    declared_commands = _declared_node_commands(services, device_id)
    if declared_commands and payload.command not in declared_commands:
        raise HTTPException(status_code=400, detail="Gateway node does not declare this command")
    command = services.gateway_runtime.node_commands.enqueue(
        device_id=device_id,
        command=payload.command,
        payload=payload.payload,
        idempotency_key=payload.idempotency_key,
        requested_by=str(request.headers.get("x-openclaw-agent-id") or "gateway-http").strip(),
        metadata=payload.metadata,
    )
    await _broadcast_gateway_event("node.command.updated", {"command": command.to_dict()}, operator_only=True)
    return {"command": command.to_dict()}


@router.get("/api/gateway/nodes/{device_id}/pending")
async def list_gateway_node_pending(
    device_id: str,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    return _gateway_node_commands_snapshot(services, device_id=device_id)


@router.post("/api/gateway/nodes/{device_id}/pending/{command_id}/ack")
async def acknowledge_gateway_node_command(
    device_id: str,
    command_id: str,
    payload: GatewayNodeAckRequest,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    command = services.gateway_runtime.node_commands.acknowledge(
        command_id,
        device_id=device_id,
        status=payload.status,
        result=payload.result,
        error=payload.error,
    )
    if command is None:
        raise HTTPException(status_code=404, detail="Gateway node command not found")
    await _broadcast_gateway_event("node.command.updated", {"command": command.to_dict()}, operator_only=True)
    return {"command": command.to_dict()}


@router.get("/api/gateway/channel-routes")
async def list_gateway_channel_routes(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    manager = _ensure_gateway_channel_routing(services)
    if manager is None or not hasattr(manager, "list_routes"):
        return {"routes": []}
    return {"routes": manager.list_routes()}


@router.post("/api/gateway/channel-routes/preview")
async def preview_gateway_channel_route(
    payload: ChannelRoutePreviewRequest,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    manager = _ensure_gateway_channel_routing(services)
    if manager is None or not hasattr(manager, "preview_route"):
        raise HTTPException(status_code=503, detail="Channel routing is unavailable")
    return manager.preview_route(payload.channel_name, payload.payload)


@router.get("/api/gateway/channels")
async def list_gateway_channels(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    manager = _system_channel_manager(services)
    if manager is None:
        return {"channels": []}
    channels: list[dict[str, Any]] = []
    for channel_name in _supported_channels(services):
        channel = manager.get_channel(channel_name)
        channels.append(
            {
                "name": channel_name,
                "class_name": type(channel).__name__ if channel is not None else "",
                "config": _redact_channel_config(getattr(channel, "config", None)),
            }
        )
    return {"channels": channels}


@router.get("/api/gateway/presence")
async def list_gateway_presence(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    return {"presence": services.gateway_runtime.presence.list()}


@router.get("/api/gateway/pairings")
async def list_gateway_pairings(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    return {
        "pending": services.gateway_runtime.pairings.list_pending(),
        "approved": services.gateway_runtime.pairings.list_approved(),
    }


@router.post("/api/gateway/pairings/{device_id}/approve")
async def approve_gateway_pairing(
    device_id: str,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    pairing = services.gateway_runtime.pairings.approve(device_id, approved_by="operator")
    if pairing is None:
        raise HTTPException(status_code=404, detail="Gateway pairing request not found")
    services.gateway_runtime.nodes.record_pairing(pairing)
    await _broadcast_gateway_event("device.pair.resolved", pairing.to_dict(), operator_only=True)
    return {"success": True, "pairing": pairing.to_dict()}


@router.post("/api/gateway/pairings/{device_id}/reject")
async def reject_gateway_pairing(
    device_id: str,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    pairing = services.gateway_runtime.pairings.reject(device_id, rejected_by="operator")
    if pairing is None:
        raise HTTPException(status_code=404, detail="Gateway pairing request not found")
    services.gateway_runtime.nodes.record_pairing(pairing)
    await _broadcast_gateway_event("device.pair.resolved", pairing.to_dict(), operator_only=True)
    return {"success": True, "pairing": pairing.to_dict()}


@router.get("/api/gateway/tools/catalog")
async def get_gateway_tools_catalog(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    if not services.llm_configured:
        return {"tools": {}}
    agent = services.system_agent()
    return {"tools": agent.list_tools(), "mode": agent.get_mode_profile().get("name", "assistant")}


@router.get("/api/gateway/approvals")
async def list_gateway_approvals(
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    snapshot = services.approval_queue.get_snapshot()
    return {
        "pending": services.approval_queue.list_pending(),
        "recent": services.approval_queue.list_history(limit=25),
        "counts": {
            "pending": snapshot["pending"],
            "approved": snapshot["approved"],
            "rejected": snapshot["rejected"],
        },
    }


from web.routers import gateway_responses as _gateway_responses_module  # noqa: E402, F401
from web.routers import gateway_websocket as _gateway_websocket_module  # noqa: E402, F401
