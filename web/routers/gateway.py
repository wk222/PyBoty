"""Gateway compatibility APIs inspired by OpenClaw/OpenResponses."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
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
from core.systems.integration.channel_runtime import ChannelMessage, ChannelRouteDecision
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
    agent = services.agents.get_or_create_mode(mode, thread_id)
    return str(agent.chat(message.message))


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


@router.websocket("/gateway/ws")
async def gateway_websocket(websocket: WebSocket) -> None:
    services: WebServices = websocket.app.state.services
    await websocket.accept()
    nonce = token_hex(16)
    challenge_ts = int(time.time() * 1000)
    connection_id = ""
    ws_client: _GatewayWsClient | None = None
    tick_task: asyncio.Task[None] | None = None

    async def send_frame(frame: dict[str, Any]) -> None:
        if ws_client is None:
            await websocket.send_json(frame)
            return
        await _send_gateway_frame(ws_client, frame)

    try:
        await send_frame(
            {
                "type": "event",
                "event": "connect.challenge",
                "payload": {"nonce": nonce, "ts": challenge_ts},
            }
        )
        first_frame = await websocket.receive_json()
        if not isinstance(first_frame, dict):
            await send_frame(
                _ws_response(
                    request_id="connect",
                    ok=False,
                    error={"code": "INVALID_FRAME", "message": "First frame must be a JSON request"},
                )
            )
            await websocket.close()
            return

        request_id = str(first_frame.get("id", "connect")).strip() or "connect"
        if first_frame.get("type") != "req" or first_frame.get("method") != "connect":
            await send_frame(
                _ws_response(
                    request_id=request_id,
                    ok=False,
                    error={"code": "INVALID_HANDSHAKE", "message": "First frame must be req/connect"},
                )
            )
            await websocket.close()
            return

        params = first_frame.get("params", {})
        if not isinstance(params, dict):
            params = {}

        gateway_config = _require_gateway_ws_auth(params)
        ws_cfg = gateway_config.get("ws", {})
        protocol_version = int(ws_cfg.get("protocol_version", _WS_PROTOCOL_VERSION))
        tick_interval_ms = int(ws_cfg.get("tick_interval_ms", 15000))
        min_protocol = int(params.get("minProtocol", protocol_version))
        max_protocol = int(params.get("maxProtocol", protocol_version))
        if not (min_protocol <= protocol_version <= max_protocol):
            await send_frame(
                _ws_response(
                    request_id=request_id,
                    ok=False,
                    error={
                        "code": "PROTOCOL_MISMATCH",
                        "message": "Gateway protocol version mismatch",
                        "details": {"protocol": protocol_version},
                    },
                )
            )
            await websocket.close()
            return

        device = params.get("device", {}) if isinstance(params.get("device"), dict) else {}
        device_id = str(device.get("id", "")).strip()
        if bool(ws_cfg.get("require_device_id", True)) and not device_id:
            await send_frame(
                _ws_response(
                    request_id=request_id,
                    ok=False,
                    error={
                        "code": "DEVICE_AUTH_REQUIRED",
                        "message": "device.id is required",
                        "details": {"reason": "device-id-missing"},
                    },
                )
            )
            await websocket.close()
            return

        role = str(params.get("role", "operator")).strip() or "operator"
        scopes = [str(item) for item in params.get("scopes", []) if str(item).strip()]
        session_key = str(params.get("sessionKey", "")).strip() or f"ws-{device_id or token_hex(4)}"
        pairing_cfg = gateway_config.get("pairing", {})
        require_paired_device_token = bool(ws_cfg.get("require_paired_device_token", False))
        if bool(pairing_cfg.get("enabled", True)) and device_id:
            is_local = _is_local_client(getattr(websocket.client, "host", ""))
            if not services.gateway_runtime.pairings.is_approved(device_id):
                existing_pairing = services.gateway_runtime.pairings.get_request(device_id)
                client = params.get("client", {}) if isinstance(params.get("client"), dict) else {}
                if bool(pairing_cfg.get("auto_approve_local", True)) and is_local:
                    pairing = services.gateway_runtime.pairings.ensure_request(
                        device_id=device_id,
                        role=role,
                        client_id=str(client.get("id", "")).strip(),
                        scopes=scopes,
                        platform=str(client.get("platform", "")).strip(),
                        mode=str(client.get("mode", "")).strip(),
                        user_agent=str(params.get("userAgent", "")).strip(),
                        metadata={"local_auto_approved": True},
                    )
                    services.gateway_runtime.nodes.record_pairing(pairing)
                    pairing = services.gateway_runtime.pairings.approve(device_id, note="auto-approved local device")
                    if pairing is not None:
                        services.gateway_runtime.nodes.record_pairing(pairing)
                    if pairing is not None and (existing_pairing is None or existing_pairing.status != "approved"):
                        await _broadcast_gateway_event("device.pair.resolved", pairing.to_dict(), operator_only=True)
                else:
                    pairing = services.gateway_runtime.pairings.ensure_request(
                        device_id=device_id,
                        role=role,
                        client_id=str(client.get("id", "")).strip(),
                        scopes=scopes,
                        platform=str(client.get("platform", "")).strip(),
                        mode=str(client.get("mode", "")).strip(),
                        user_agent=str(params.get("userAgent", "")).strip(),
                        metadata={"remote_addr": getattr(websocket.client, "host", "")},
                    )
                    services.gateway_runtime.nodes.record_pairing(pairing)
                    if existing_pairing is None or existing_pairing.status != "pending":
                        await _broadcast_gateway_event("device.pair.requested", pairing.to_dict(), operator_only=True)
                    await send_frame(
                        _ws_response(
                            request_id=request_id,
                            ok=False,
                            error={
                                "code": "PAIRING_REQUIRED",
                                "message": "Device pairing approval required",
                                "details": {
                                    "code": "PAIRING_REQUIRED",
                                    "reason": "device-not-approved",
                                    "requestId": pairing.request_id,
                                    "pairing": pairing.to_dict(),
                                },
                            },
                        )
                    )
                    await websocket.close()
                    return
            elif require_paired_device_token:
                supplied_device_token = _extract_gateway_device_token(params)
                if not services.gateway_runtime.pairings.validate_device_token(device_id, supplied_device_token):
                    pairing = services.gateway_runtime.pairings.get_request(device_id)
                    await send_frame(
                        _ws_response(
                            request_id=request_id,
                            ok=False,
                            error={
                                "code": "DEVICE_TOKEN_REQUIRED",
                                "message": "Paired device token is required",
                                "details": {
                                    "reason": "device-token-invalid",
                                    "deviceId": device_id,
                                    "pairing": pairing.to_dict() if pairing is not None else None,
                                },
                            },
                        )
                    )
                    await websocket.close()
                    return

        connection_id = services.gateway_runtime.new_connection_id()
        presence_entry = _build_presence_entry(
            connection_id=connection_id,
            params=params,
            session_key=session_key,
        )
        services.gateway_runtime.presence.register(presence_entry)
        services.gateway_runtime.sessions.touch(
            session_key,
            source="ws",
            device_id=presence_entry.device_id,
            client_id=presence_entry.client_id,
            metadata={
                "role": role,
                "client_mode": presence_entry.mode,
                "user_agent": presence_entry.user_agent,
            },
        )
        prepared_session = prepare_gateway_request(
            OpenResponsesRequest(
                model=f"pybot:{presence_entry.mode or 'assistant'}",
                input="gateway connection bootstrap",
            ),
            session_key=session_key,
        )
        services.session_runtime.bind_gateway_session(
            session_key=prepared_session.session_key,
            thread_id=prepared_session.thread_id,
            mode=prepared_session.resolved_mode,
            source="ws",
            device_id=presence_entry.device_id,
            client_id=presence_entry.client_id,
            metadata={
                "role": role,
                "client_mode": presence_entry.mode,
                "user_agent": presence_entry.user_agent,
            },
        )
        services.gateway_runtime.nodes.touch_from_presence(
            presence_entry,
            approved=bool(device_id and services.gateway_runtime.pairings.is_approved(device_id)),
        )
        ws_client = _GatewayWsClient(
            connection_id=connection_id,
            websocket=websocket,
            role=presence_entry.role,
            scopes=tuple(presence_entry.scopes),
        )
        _register_gateway_ws_client(ws_client)

        hello_payload: dict[str, Any] = {
            "type": "hello-ok",
            "protocol": protocol_version,
            "policy": {
                "tickIntervalMs": tick_interval_ms,
                "pairingRequired": bool(pairing_cfg.get("enabled", True)),
                "pairedDeviceTokenRequired": bool(ws_cfg.get("require_paired_device_token", False)),
            },
            "features": _gateway_features(services),
            "snapshot": {
                "presence": _gateway_presence_snapshot(services),
                "sessions": {"items": _gateway_session_items(services)},
                "runs": _gateway_runs_snapshot(services),
                "nodes": {"items": services.gateway_runtime.nodes.list()},
            },
            "connection": {"id": connection_id, "sessionKey": session_key},
            "auth": {
                "role": role,
                "scopes": scopes,
            },
        }
        if ws_client.is_operator:
            hello_payload["snapshot"]["pairings"] = _gateway_pairing_snapshot(services)

        await send_frame(
            _ws_response(
                request_id=request_id,
                ok=True,
                payload=hello_payload,
            )
        )

        await _broadcast_gateway_event(
            "presence",
            {"presence": services.gateway_runtime.presence.list()},
            operator_only=True,
            exclude_connection_ids={connection_id},
        )

        async def tick_loop() -> None:
            interval_seconds = max(tick_interval_ms, 1) / 1000
            while True:
                await asyncio.sleep(interval_seconds)
                await send_frame(
                    {
                        "type": "event",
                        "event": "tick",
                        "payload": {"ts": int(time.time() * 1000), "connectionId": connection_id},
                    }
                )

        tick_task = asyncio.create_task(tick_loop())

        while True:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                continue
            req_id = str(frame.get("id", "")).strip() or f"req_{token_hex(4)}"
            method = str(frame.get("method", "")).strip()
            params = frame.get("params", {}) if isinstance(frame.get("params"), dict) else {}
            services.gateway_runtime.presence.touch(connection_id)

            if frame.get("type") != "req":
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=False,
                        error={"code": "INVALID_FRAME", "message": "Frame must be a request"},
                    )
                )
                continue

            if method == "ping":
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={"pong": True, "ts": int(time.time() * 1000)},
                    )
                )
                continue
            if method in {"system.presence", "system-presence"}:
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload=_gateway_presence_snapshot(services),
                    )
                )
                continue
            if method in {"pairings.list", "device.pair.list"}:
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload=_gateway_pairing_snapshot(services),
                    )
                )
                continue
            if method == "nodes.list":
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={"nodes": services.gateway_runtime.nodes.list()},
                    )
                )
                continue
            if method == "nodes.get":
                lookup_device_id = str(params.get("device_id", "")).strip()
                node = services.gateway_runtime.nodes.get(lookup_device_id)
                if node is None:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "NODE_NOT_FOUND", "message": "Gateway node not found"},
                        )
                    )
                    continue
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={
                            "node": node.to_dict(),
                            "presence": services.gateway_runtime.presence.by_device(lookup_device_id),
                        },
                    )
                )
                continue
            if method == "node.invoke":
                target_device_id = str(params.get("device_id", "")).strip()
                command_name = str(params.get("command", "")).strip()
                if not target_device_id or not command_name:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "INVALID_NODE_COMMAND", "message": "device_id and command are required"},
                        )
                    )
                    continue
                node = services.gateway_runtime.nodes.get(target_device_id)
                if node is None:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "NODE_NOT_FOUND", "message": "Gateway node not found"},
                        )
                    )
                    continue
                declared_commands = _declared_node_commands(services, target_device_id)
                if declared_commands and command_name not in declared_commands:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "COMMAND_NOT_ALLOWED", "message": "Node does not declare this command"},
                        )
                    )
                    continue
                payload = params.get("payload", {})
                if not isinstance(payload, dict):
                    payload = {}
                metadata = params.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                command = services.gateway_runtime.node_commands.enqueue(
                    device_id=target_device_id,
                    command=command_name,
                    payload=payload,
                    idempotency_key=str(params.get("idempotency_key", "")).strip(),
                    requested_by=presence_entry.device_id or presence_entry.client_id or "gateway-operator",
                    metadata=metadata,
                )
                await send_frame(_ws_response(request_id=req_id, ok=True, payload={"command": command.to_dict()}))
                await _broadcast_gateway_event(
                    "node.command.updated",
                    {"command": command.to_dict()},
                    operator_only=True,
                )
                continue
            if method in {"device.pair.approve", "pairings.approve"}:
                pairing_id = _resolve_pairing_identifier(params)
                if not pairing_id:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "MISSING_DEVICE_ID", "message": "request_id or device_id is required"},
                        )
                    )
                    continue
                pairing = services.gateway_runtime.pairings.approve(
                    pairing_id,
                    note=str(params.get("note", "")),
                    approved_by=presence_entry.device_id or presence_entry.client_id or "gateway-operator",
                )
                if pairing is None:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "PAIRING_NOT_FOUND", "message": "Gateway pairing request not found"},
                        )
                    )
                    continue
                services.gateway_runtime.nodes.record_pairing(pairing)
                await send_frame(_ws_response(request_id=req_id, ok=True, payload=pairing.to_dict()))
                await _broadcast_gateway_event("device.pair.resolved", pairing.to_dict(), operator_only=True)
                continue
            if method in {"device.pair.reject", "pairings.reject"}:
                pairing_id = _resolve_pairing_identifier(params)
                if not pairing_id:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "MISSING_DEVICE_ID", "message": "request_id or device_id is required"},
                        )
                    )
                    continue
                pairing = services.gateway_runtime.pairings.reject(
                    pairing_id,
                    note=str(params.get("note", "")),
                    rejected_by=presence_entry.device_id or presence_entry.client_id or "gateway-operator",
                )
                if pairing is None:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "PAIRING_NOT_FOUND", "message": "Gateway pairing request not found"},
                        )
                    )
                    continue
                services.gateway_runtime.nodes.record_pairing(pairing)
                await send_frame(_ws_response(request_id=req_id, ok=True, payload=pairing.to_dict()))
                await _broadcast_gateway_event("device.pair.resolved", pairing.to_dict(), operator_only=True)
                continue
            if method == "approvals.list":
                snapshot = services.approval_queue.get_snapshot()
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={
                            "pending": services.approval_queue.list_pending(),
                            "recent": services.approval_queue.list_history(limit=25),
                            "counts": {
                                "pending": snapshot["pending"],
                                "approved": snapshot["approved"],
                                "rejected": snapshot["rejected"],
                            },
                        },
                    )
                )
                continue
            if method == "exec.approval.resolve":
                approval_id = str(params.get("approval_id", "")).strip()
                if not approval_id:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "MISSING_APPROVAL_ID", "message": "approval_id is required"},
                        )
                    )
                    continue
                try:
                    result = services.approvals.resolve(
                        approval_id,
                        approved=bool(params.get("approved", True)),
                        note=str(params.get("note", "")),
                        approver=str(presence_entry.device_id or presence_entry.client_id or "gateway-operator"),
                        resolution_labels=params.get("labels", []),
                    )
                except Exception as exc:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "APPROVAL_RESOLVE_FAILED", "message": str(exc)},
                        )
                    )
                    continue
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=bool(result.get("success")),
                        payload=result,
                    )
                )
                continue
            if method == "channels.list":
                manager = _system_channel_manager(services)
                payload = []
                if manager is not None:
                    for channel_name in _supported_channels(services):
                        channel = manager.get_channel(channel_name)
                        payload.append(
                            {
                                "name": channel_name,
                                "class_name": type(channel).__name__ if channel is not None else "",
                                "config": _redact_channel_config(getattr(channel, "config", None)),
                            }
                        )
                await send_frame(_ws_response(request_id=req_id, ok=True, payload={"channels": payload}))
                continue
            if method == "channel-routes.list":
                manager = _ensure_gateway_channel_routing(services)
                routes = manager.list_routes() if manager is not None and hasattr(manager, "list_routes") else []
                await send_frame(_ws_response(request_id=req_id, ok=True, payload={"routes": routes}))
                continue
            if method == "channel-routes.preview":
                manager = _ensure_gateway_channel_routing(services)
                if manager is None or not hasattr(manager, "preview_route"):
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "ROUTING_UNAVAILABLE", "message": "Channel routing is unavailable"},
                        )
                    )
                    continue
                preview_payload = params.get("payload", {})
                if not isinstance(preview_payload, dict):
                    preview_payload = {}
                preview = manager.preview_route(str(params.get("channel_name", "")).strip(), preview_payload)
                await send_frame(_ws_response(request_id=req_id, ok=True, payload=preview))
                continue
            if method == "sessions.list":
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={"sessions": _gateway_session_items(services)},
                    )
                )
                continue
            if method == "sessions.get":
                mode = str(params.get("mode", presence_entry.mode or "assistant")).strip() or "assistant"
                session_lookup_key = str(params.get("session_key", "")).strip() or presence_entry.session_key
                prepared = prepare_gateway_request(
                    OpenResponsesRequest(model=f"pybot:{mode}", input="session lookup"),
                    session_key=session_lookup_key,
                )
                session_meta = services.gateway_runtime.sessions.get(prepared.session_key)
                latest_run = services.gateway_runtime.runs.latest_for_session(prepared.session_key)
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={
                            "thread_id": prepared.thread_id,
                            "mode": prepared.resolved_mode,
                            "session_key": prepared.session_key,
                            "session_meta": session_meta.to_dict() if session_meta is not None else None,
                            "presence": services.gateway_runtime.presence.by_session(prepared.session_key),
                            "history": services.conversations.get_history(prepared.thread_id),
                            "latest_run": latest_run.to_dict() if latest_run is not None else None,
                        },
                    )
                )
                continue
            if method == "runs.list":
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload=_gateway_runs_snapshot(services),
                    )
                )
                continue
            if method == "runs.get":
                run_id = str(params.get("run_id", "")).strip()
                record = services.gateway_runtime.runs.get(run_id)
                if record is None:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "RUN_NOT_FOUND", "message": "Gateway run not found"},
                        )
                    )
                    continue
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={"run": record.to_dict(), "response": _payload_from_run_record(record)},
                    )
                )
                continue
            if method in {"runs.abort", "chat.abort"}:
                run_id = str(params.get("run_id", "")).strip()
                note = str(params.get("note", "")).strip()
                requested_by = presence_entry.device_id or presence_entry.client_id or "gateway-operator"
                record = None
                if run_id:
                    record = services.gateway_runtime.runs.request_abort(run_id, note=note, requested_by=requested_by)
                if record is None:
                    session_lookup_key = str(params.get("session_key", "")).strip() or presence_entry.session_key
                    record = services.gateway_runtime.runs.request_abort_for_session(
                        session_lookup_key,
                        note=note,
                        requested_by=requested_by,
                    )
                if record is None:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "RUN_NOT_FOUND", "message": "No active gateway run found"},
                        )
                    )
                    continue
                await send_frame(_ws_response(request_id=req_id, ok=True, payload={"run": record.to_dict()}))
                await _broadcast_gateway_event("run.updated", {"run": record.to_dict()}, operator_only=True)
                continue
            if method == "node.pending.pull":
                limit = params.get("limit", 10)
                try:
                    limit_value = max(1, int(limit))
                except (TypeError, ValueError):
                    limit_value = 10
                pulled = services.gateway_runtime.node_commands.pull(
                    device_id=presence_entry.device_id,
                    connection_id=presence_entry.connection_id,
                    limit=limit_value,
                )
                payload = {"commands": [item.to_dict() for item in pulled]}
                await send_frame(_ws_response(request_id=req_id, ok=True, payload=payload))
                for command in pulled:
                    await _broadcast_gateway_event(
                        "node.command.updated",
                        {"command": command.to_dict()},
                        operator_only=True,
                    )
                continue
            if method == "node.pending.ack":
                command_id = str(params.get("command_id", "")).strip()
                if not command_id:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "MISSING_COMMAND_ID", "message": "command_id is required"},
                        )
                    )
                    continue
                result = params.get("result", {})
                if not isinstance(result, dict):
                    result = {}
                try:
                    command = services.gateway_runtime.node_commands.acknowledge(
                        command_id,
                        device_id=presence_entry.device_id,
                        status=str(params.get("status", "completed")).strip() or "completed",
                        result=result,
                        error=str(params.get("error", "")),
                    )
                except ValueError as exc:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "INVALID_COMMAND_STATUS", "message": str(exc)},
                        )
                    )
                    continue
                if command is None:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "COMMAND_NOT_FOUND", "message": "Gateway node command not found"},
                        )
                    )
                    continue
                await send_frame(_ws_response(request_id=req_id, ok=True, payload={"command": command.to_dict()}))
                await _broadcast_gateway_event(
                    "node.command.updated",
                    {"command": command.to_dict()},
                    operator_only=True,
                )
                continue
            if method == "tools.catalog":
                mode = str(params.get("mode", "assistant")).strip() or "assistant"
                agent = _require_gateway_agent(services, root_mode=mode, thread_id=f"gateway-{mode}-catalog")
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={"tools": agent.list_tools(), "mode": agent.get_mode_profile().get("name", mode)},
                    )
                )
                continue
            if method == "chat.history":
                mode = str(params.get("mode", presence_entry.mode or "assistant")).strip() or "assistant"
                session_lookup_key = str(params.get("session_key", "")).strip() or presence_entry.session_key
                prepared = prepare_gateway_request(
                    OpenResponsesRequest(model=f"pybot:{mode}", input="history lookup"),
                    session_key=session_lookup_key,
                )
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={
                            "thread_id": prepared.thread_id,
                            "mode": prepared.resolved_mode,
                            "session_key": prepared.session_key,
                            "history": services.conversations.get_history(prepared.thread_id),
                        },
                    )
                )
                continue
            if method == "chat.inject":
                mode = str(params.get("mode", presence_entry.mode or "assistant")).strip() or "assistant"
                session_lookup_key = str(params.get("session_key", "")).strip() or presence_entry.session_key
                prepared = prepare_gateway_request(
                    OpenResponsesRequest(model=f"pybot:{mode}", input="inject"),
                    session_key=session_lookup_key,
                )
                items = params.get("messages", [])
                if not isinstance(items, list) or not items:
                    message = str(params.get("message", "")).strip()
                    role = str(params.get("role", "system")).strip() or "system"
                    items = [{"role": role, "content": message}] if message else []
                injected = _inject_session_messages(services, prepared=prepared, items=items)
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload={
                            "thread_id": prepared.thread_id,
                            "mode": prepared.resolved_mode,
                            "session_key": prepared.session_key,
                            "injected": injected,
                        },
                    )
                )
                continue
            if method == "chat.send":
                message = str(params.get("message", "")).strip()
                if not message:
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=False,
                            error={"code": "MISSING_MESSAGE", "message": "message is required"},
                        )
                    )
                    continue
                prepared = prepare_gateway_request(
                    OpenResponsesRequest(
                        model=str(params.get("model", f"pybot:{presence_entry.mode or 'assistant'}")),
                        input=message,
                        instructions=str(params.get("instructions", "")).strip() or None,
                        user=str(params.get("user", presence_entry.device_id or presence_entry.client_id or "")).strip()
                        or None,
                    ),
                    session_key=str(params.get("session_key", "")).strip() or presence_entry.session_key,
                    header_agent_id=str(params.get("agent_id", "")).strip() or None,
                )
                agent = _require_gateway_agent(
                    services,
                    root_mode=prepared.resolved_mode,
                    thread_id=prepared.thread_id,
                )
                services.gateway_runtime.sessions.touch(
                    prepared.session_key,
                    mode=prepared.resolved_mode,
                    thread_id=prepared.thread_id,
                    source="ws.chat",
                    user=str(params.get("user", "")).strip(),
                    device_id=presence_entry.device_id,
                    client_id=presence_entry.client_id,
                )
                services.session_runtime.bind_gateway_session(
                    session_key=prepared.session_key,
                    thread_id=prepared.thread_id,
                    mode=prepared.resolved_mode,
                    source="ws.chat",
                    user=str(params.get("user", "")).strip(),
                    device_id=presence_entry.device_id,
                    client_id=presence_entry.client_id,
                )
                services.conversations.ensure_conversation(prepared.thread_id, title_hint=prepared.display_input)
                services.conversations.append_message(prepared.thread_id, "user", prepared.display_input)
                services.session_runtime.record_message(
                    thread_id=prepared.thread_id,
                    role="user",
                    content=prepared.display_input,
                    root_mode=prepared.resolved_mode,
                    source="ws.chat",
                    session_key=prepared.session_key,
                )
                services.gateway_runtime.runs.start(
                    run_id=prepared.response_id,
                    response_id=prepared.response_id,
                    session_key=prepared.session_key,
                    thread_id=prepared.thread_id,
                    mode=prepared.resolved_mode,
                    requested_model=prepared.requested_model,
                    source="ws.chat",
                    display_input=prepared.display_input,
                    user=str(params.get("user", "")).strip(),
                    requested_by=presence_entry.device_id or presence_entry.client_id or "gateway-operator",
                    metadata=prepared.metadata,
                    ignored_features=prepared.ignored_features,
                    client_tools=prepared.client_tools,
                )
                services.session_runtime.record_run(
                    session_key=prepared.session_key,
                    thread_id=prepared.thread_id,
                    run_id=prepared.response_id,
                    mode=prepared.resolved_mode,
                    status="in_progress",
                    source="ws.chat",
                    requested_model=prepared.requested_model,
                    display_input=prepared.display_input,
                    metadata=prepared.metadata,
                )
                await _broadcast_gateway_event(
                    "run.updated",
                    {"run": services.gateway_runtime.runs.get(prepared.response_id).to_dict()},
                    operator_only=True,
                )
                output_text = str(await asyncio.to_thread(agent.chat, prepared.prompt))
                if services.gateway_runtime.runs.is_abort_requested(prepared.response_id):
                    cancelled_payload = _cancelled_run_payload(
                        services.gateway_runtime.runs.get(prepared.response_id),
                        note="Run cancelled by operator",
                    )
                    services.gateway_runtime.runs.mark_cancelled(
                        prepared.response_id,
                        note="Run cancelled by operator",
                        cancelled_by=presence_entry.device_id or presence_entry.client_id or "gateway-operator",
                        response_payload=cancelled_payload,
                    )
                    services.session_runtime.record_run(
                        session_key=prepared.session_key,
                        thread_id=prepared.thread_id,
                        run_id=prepared.response_id,
                        mode=prepared.resolved_mode,
                        status="cancelled",
                        source="ws.chat",
                        requested_model=prepared.requested_model,
                        display_input=prepared.display_input,
                        metadata=prepared.metadata,
                    )
                    await send_frame(
                        _ws_response(
                            request_id=req_id,
                            ok=True,
                            payload=cancelled_payload,
                        )
                    )
                    await _broadcast_gateway_event(
                        "run.updated",
                        {"run": services.gateway_runtime.runs.get(prepared.response_id).to_dict()},
                        operator_only=True,
                    )
                    continue
                services.conversations.append_message(prepared.thread_id, "assistant", output_text)
                services.session_runtime.record_message(
                    thread_id=prepared.thread_id,
                    role="assistant",
                    content=output_text,
                    root_mode=prepared.resolved_mode,
                    source="ws.chat",
                    session_key=prepared.session_key,
                )
                response_payload = build_openresponses_payload(prepared=prepared, output_text=output_text)
                services.gateway_runtime.runs.complete(
                    prepared.response_id,
                    output_text=output_text,
                    response_payload=response_payload,
                )
                services.session_runtime.record_run(
                    session_key=prepared.session_key,
                    thread_id=prepared.thread_id,
                    run_id=prepared.response_id,
                    mode=prepared.resolved_mode,
                    status="completed",
                    source="ws.chat",
                    requested_model=prepared.requested_model,
                    display_input=prepared.display_input,
                    output_text=output_text,
                    metadata=prepared.metadata,
                )
                await send_frame(
                    _ws_response(
                        request_id=req_id,
                        ok=True,
                        payload=response_payload,
                    )
                )
                await _broadcast_gateway_event(
                    "run.updated",
                    {"run": services.gateway_runtime.runs.get(prepared.response_id).to_dict()},
                    operator_only=True,
                )
                continue

            await send_frame(
                _ws_response(
                    request_id=req_id,
                    ok=False,
                    error={"code": "UNKNOWN_METHOD", "message": f"Unknown gateway method: {method}"},
                )
            )
    except WebSocketDisconnect:
        pass
    except HTTPException as exc:
        try:
            await send_frame(
                _ws_response(
                    request_id="connect",
                    ok=False,
                    error={"code": "AUTH_FAILED", "message": str(exc.detail)},
                )
            )
        except Exception:
            pass
    finally:
        if tick_task is not None:
            tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tick_task
        if connection_id:
            services.gateway_runtime.presence.remove(connection_id)
            _unregister_gateway_ws_client(connection_id)
            with contextlib.suppress(Exception):
                await _broadcast_gateway_event(
                    "presence",
                    {"presence": services.gateway_runtime.presence.list()},
                    operator_only=True,
                )
        try:
            await websocket.close()
        except Exception:
            pass


@router.post("/v1/responses", response_model=None)
async def create_gateway_response(
    payload: OpenResponsesRequest,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any] | StreamingResponse:
    gateway_config = _require_gateway_auth(request)
    responses_cfg = gateway_config.get("http", {}).get("endpoints", {}).get("responses", {})
    if not bool(responses_cfg.get("enabled", False)):
        raise HTTPException(status_code=404, detail="Gateway responses endpoint is disabled")

    prepared = _resolve_gateway_request_context(
        services,
        payload=payload,
        session_key=request.headers.get("x-openclaw-session-key"),
        header_agent_id=request.headers.get("x-openclaw-agent-id"),
    )
    services.gateway_runtime.sessions.touch(
        prepared.session_key,
        mode=prepared.resolved_mode,
        thread_id=prepared.thread_id,
        source="http.responses",
        user=str(payload.user or "").strip(),
        metadata={"model": prepared.requested_model},
    )
    services.session_runtime.bind_gateway_session(
        session_key=prepared.session_key,
        thread_id=prepared.thread_id,
        mode=prepared.resolved_mode,
        source="http.responses",
        user=str(payload.user or "").strip(),
        metadata={"model": prepared.requested_model},
    )
    services.conversations.ensure_conversation(prepared.thread_id, title_hint=prepared.display_input)
    services.conversations.append_message(
        prepared.thread_id,
        "user",
        prepared.display_input,
        title_hint=prepared.display_input,
    )
    services.session_runtime.record_message(
        thread_id=prepared.thread_id,
        role="user",
        content=prepared.display_input,
        root_mode=prepared.resolved_mode,
        source="http.responses",
        session_key=prepared.session_key,
    )
    services.gateway_runtime.runs.start(
        run_id=prepared.response_id,
        response_id=prepared.response_id,
        session_key=prepared.session_key,
        thread_id=prepared.thread_id,
        mode=prepared.resolved_mode,
        requested_model=prepared.requested_model,
        source="http.responses",
        display_input=prepared.display_input,
        user=str(payload.user or "").strip(),
        requested_by=str(request.headers.get("x-openclaw-agent-id") or "").strip(),
        metadata=prepared.metadata,
        ignored_features=prepared.ignored_features,
        client_tools=prepared.client_tools,
    )
    services.session_runtime.record_run(
        session_key=prepared.session_key,
        thread_id=prepared.thread_id,
        run_id=prepared.response_id,
        mode=prepared.resolved_mode,
        status="in_progress",
        source="http.responses",
        requested_model=prepared.requested_model,
        display_input=prepared.display_input,
        metadata=prepared.metadata,
    )
    await _broadcast_gateway_event(
        "run.updated",
        {"run": services.gateway_runtime.runs.get(prepared.response_id).to_dict()},
        operator_only=True,
    )

    selected_client_tool = None
    if payload.tools and not request_has_function_call_output(payload):
        selected_client_tool = select_client_tool_name(payload, prepared)

    if selected_client_tool:
        tool_payload = build_openresponses_tool_call_payload(
            prepared=prepared,
            tool_name=selected_client_tool,
            arguments=_gateway_tool_call_arguments(prepared=prepared, payload=payload),
        )
        services.gateway_runtime.runs.complete(
            prepared.response_id,
            output_text="",
            response_payload=tool_payload,
            status="incomplete",
        )
        services.session_runtime.record_run(
            session_key=prepared.session_key,
            thread_id=prepared.thread_id,
            run_id=prepared.response_id,
            mode=prepared.resolved_mode,
            status="incomplete",
            source="http.responses",
            requested_model=prepared.requested_model,
            display_input=prepared.display_input,
            metadata=prepared.metadata,
        )
        if payload.stream:
            if not bool(responses_cfg.get("stream_enabled", False)):
                raise HTTPException(status_code=400, detail="Gateway streaming is disabled")

            def tool_event_generator() -> Iterator[str]:
                yield _sse("response.created", build_openresponses_created_event(prepared))
                yield _sse(
                    "response.in_progress",
                    {"response_id": prepared.response_id, "status": "in_progress"},
                )
                yield _sse(
                    "response.output_item.added",
                    {
                        "response_id": prepared.response_id,
                        "item": tool_payload["output"][0],
                    },
                )
                yield _sse(
                    "response.output_item.done",
                    {
                        "response_id": prepared.response_id,
                        "item": tool_payload["output"][0],
                    },
                )
                yield _sse("response.completed", tool_payload)
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                tool_event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        return tool_payload

    agent = _require_gateway_agent(
        services,
        root_mode=prepared.resolved_mode,
        thread_id=prepared.thread_id,
    )

    if payload.stream:
        if not bool(responses_cfg.get("stream_enabled", False)):
            raise HTTPException(status_code=400, detail="Gateway streaming is disabled")

        def event_generator() -> Iterator[str]:
            yield _sse("response.created", build_openresponses_created_event(prepared))
            for event in agent.chat_stream(prepared.prompt):
                if services.gateway_runtime.runs.is_abort_requested(prepared.response_id):
                    cancelled_payload = _cancelled_run_payload(
                        services.gateway_runtime.runs.get(prepared.response_id),
                        note="Run cancelled by operator",
                    )
                    services.gateway_runtime.runs.mark_cancelled(
                        prepared.response_id,
                        note="Run cancelled by operator",
                        response_payload=cancelled_payload,
                    )
                    services.session_runtime.record_run(
                        session_key=prepared.session_key,
                        thread_id=prepared.thread_id,
                        run_id=prepared.response_id,
                        mode=prepared.resolved_mode,
                        status="cancelled",
                        source="http.responses",
                        requested_model=prepared.requested_model,
                        display_input=prepared.display_input,
                        metadata=prepared.metadata,
                    )
                    yield _sse("response.cancelled", cancelled_payload)
                    yield "data: [DONE]\n\n"
                    return
                event_type = str(event.get("type", "")).strip()
                if event_type == "step":
                    yield _sse(
                        "pybot.step",
                        {
                            "response_id": prepared.response_id,
                            "content": str(event.get("content", "")),
                            "icon": str(event.get("icon", "")),
                        },
                    )
                    continue
                if event_type == "schedule":
                    yield _sse("pybot.schedule", {"response_id": prepared.response_id})
                    continue
                if event_type == "done":
                    output_text = str(event.get("content", ""))
                    if services.gateway_runtime.runs.is_abort_requested(prepared.response_id):
                        cancelled_payload = _cancelled_run_payload(
                            services.gateway_runtime.runs.get(prepared.response_id),
                            note="Run cancelled by operator",
                        )
                        services.gateway_runtime.runs.mark_cancelled(
                            prepared.response_id,
                            note="Run cancelled by operator",
                            response_payload=cancelled_payload,
                        )
                        yield _sse("response.cancelled", cancelled_payload)
                        yield "data: [DONE]\n\n"
                        return
                    services.conversations.append_message(prepared.thread_id, "assistant", output_text)
                    services.session_runtime.record_message(
                        thread_id=prepared.thread_id,
                        role="assistant",
                        content=output_text,
                        root_mode=prepared.resolved_mode,
                        source="http.responses",
                        session_key=prepared.session_key,
                    )
                    completed_payload = build_openresponses_payload(prepared=prepared, output_text=output_text)
                    services.gateway_runtime.runs.complete(
                        prepared.response_id,
                        output_text=output_text,
                        response_payload=completed_payload,
                    )
                    services.session_runtime.record_run(
                        session_key=prepared.session_key,
                        thread_id=prepared.thread_id,
                        run_id=prepared.response_id,
                        mode=prepared.resolved_mode,
                        status="completed",
                        source="http.responses",
                        requested_model=prepared.requested_model,
                        display_input=prepared.display_input,
                        output_text=output_text,
                        metadata=prepared.metadata,
                    )
                    yield _sse(
                        "response.output_text.delta",
                        {"response_id": prepared.response_id, "delta": output_text},
                    )
                    yield _sse("response.completed", completed_payload)
                    yield "data: [DONE]\n\n"
                    return
                if event_type == "error":
                    error_message = str(event.get("content", "Gateway execution failed"))
                    services.gateway_runtime.runs.complete(
                        prepared.response_id,
                        output_text="",
                        status="failed",
                        error=error_message,
                    )
                    services.session_runtime.record_run(
                        session_key=prepared.session_key,
                        thread_id=prepared.thread_id,
                        run_id=prepared.response_id,
                        mode=prepared.resolved_mode,
                        status="failed",
                        source="http.responses",
                        requested_model=prepared.requested_model,
                        display_input=prepared.display_input,
                        metadata={**prepared.metadata, "error": error_message},
                    )
                    yield _sse(
                        "response.failed",
                        {
                            "response_id": prepared.response_id,
                            "error": error_message,
                        },
                    )
                    yield "data: [DONE]\n\n"
                    return

            if services.gateway_runtime.runs.is_abort_requested(prepared.response_id):
                cancelled_payload = _cancelled_run_payload(
                    services.gateway_runtime.runs.get(prepared.response_id),
                    note="Run cancelled by operator",
                )
                services.gateway_runtime.runs.mark_cancelled(
                    prepared.response_id,
                    note="Run cancelled by operator",
                    response_payload=cancelled_payload,
                )
                services.session_runtime.record_run(
                    session_key=prepared.session_key,
                    thread_id=prepared.thread_id,
                    run_id=prepared.response_id,
                    mode=prepared.resolved_mode,
                    status="cancelled",
                    source="http.responses",
                    requested_model=prepared.requested_model,
                    display_input=prepared.display_input,
                    metadata=prepared.metadata,
                )
                yield _sse("response.cancelled", cancelled_payload)
                yield "data: [DONE]\n\n"
                return
            fallback_text = "（无回复）"
            services.conversations.append_message(prepared.thread_id, "assistant", fallback_text)
            services.session_runtime.record_message(
                thread_id=prepared.thread_id,
                role="assistant",
                content=fallback_text,
                root_mode=prepared.resolved_mode,
                source="http.responses",
                session_key=prepared.session_key,
            )
            fallback_payload = build_openresponses_payload(prepared=prepared, output_text=fallback_text)
            services.gateway_runtime.runs.complete(
                prepared.response_id,
                output_text=fallback_text,
                response_payload=fallback_payload,
            )
            services.session_runtime.record_run(
                session_key=prepared.session_key,
                thread_id=prepared.thread_id,
                run_id=prepared.response_id,
                mode=prepared.resolved_mode,
                status="completed",
                source="http.responses",
                requested_model=prepared.requested_model,
                display_input=prepared.display_input,
                output_text=fallback_text,
                metadata=prepared.metadata,
            )
            yield _sse(
                "response.completed",
                fallback_payload,
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    output_text = str(await asyncio.to_thread(agent.chat, prepared.prompt))
    if services.gateway_runtime.runs.is_abort_requested(prepared.response_id):
        cancelled_payload = _cancelled_run_payload(
            services.gateway_runtime.runs.get(prepared.response_id),
            note="Run cancelled by operator",
        )
        services.gateway_runtime.runs.mark_cancelled(
            prepared.response_id,
            note="Run cancelled by operator",
            response_payload=cancelled_payload,
        )
        services.session_runtime.record_run(
            session_key=prepared.session_key,
            thread_id=prepared.thread_id,
            run_id=prepared.response_id,
            mode=prepared.resolved_mode,
            status="cancelled",
            source="http.responses",
            requested_model=prepared.requested_model,
            display_input=prepared.display_input,
            metadata=prepared.metadata,
        )
        await _broadcast_gateway_event(
            "run.updated",
            {"run": services.gateway_runtime.runs.get(prepared.response_id).to_dict()},
            operator_only=True,
        )
        return cancelled_payload
    services.conversations.append_message(prepared.thread_id, "assistant", output_text)
    services.session_runtime.record_message(
        thread_id=prepared.thread_id,
        role="assistant",
        content=output_text,
        root_mode=prepared.resolved_mode,
        source="http.responses",
        session_key=prepared.session_key,
    )
    response_payload = build_openresponses_payload(prepared=prepared, output_text=output_text)
    services.gateway_runtime.runs.complete(
        prepared.response_id,
        output_text=output_text,
        response_payload=response_payload,
    )
    services.session_runtime.record_run(
        session_key=prepared.session_key,
        thread_id=prepared.thread_id,
        run_id=prepared.response_id,
        mode=prepared.resolved_mode,
        status="completed",
        source="http.responses",
        requested_model=prepared.requested_model,
        display_input=prepared.display_input,
        output_text=output_text,
        metadata=prepared.metadata,
    )
    await _broadcast_gateway_event(
        "run.updated",
        {"run": services.gateway_runtime.runs.get(prepared.response_id).to_dict()},
        operator_only=True,
    )
    return response_payload


@router.get("/v1/responses/{response_id}", response_model=None)
async def get_gateway_response(
    response_id: str,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    if not _gateway_endpoint_enabled("responses"):
        raise HTTPException(status_code=404, detail="Gateway responses endpoint is disabled")
    record = services.gateway_runtime.runs.get_by_response(response_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Gateway response not found")
    return _payload_from_run_record(record)


@router.post("/v1/responses/{response_id}/cancel", response_model=None)
@router.delete("/v1/responses/{response_id}", response_model=None)
async def cancel_gateway_response(
    response_id: str,
    request: Request,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    _require_gateway_auth(request)
    if not _gateway_endpoint_enabled("responses"):
        raise HTTPException(status_code=404, detail="Gateway responses endpoint is disabled")
    record = services.gateway_runtime.runs.request_abort(
        response_id,
        note="Response cancelled via API",
        requested_by=str(request.headers.get("x-openclaw-agent-id") or "gateway-http").strip(),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Gateway response not found")
    await _broadcast_gateway_event("run.updated", {"run": record.to_dict()}, operator_only=True)
    return {"id": response_id, "object": "response", "status": record.status, "pybot": {"run_id": response_id}}
