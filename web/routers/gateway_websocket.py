"""Gateway WebSocket endpoint.

Split out of :mod:`web.routers.gateway` to keep individual modules under the
1500-line ceiling. Imports the shared ``router`` and helper functions from the
sibling module; importing this module is sufficient to register the endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from secrets import token_hex
from typing import Any

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from core.systems.integration.openresponses import (
    OpenResponsesRequest,
    build_openresponses_payload,
    prepare_gateway_request,
)
from core.systems.runtime import get_gateway_config
from web.routers.gateway import (
    _GatewayWsClient,
    _WS_PROTOCOL_VERSION,
    _broadcast_gateway_event,
    _build_presence_entry,
    _cancelled_run_payload,
    _declared_node_commands,
    _ensure_gateway_channel_routing,
    _extract_gateway_device_token,
    _gateway_features,
    _gateway_node_commands_snapshot,
    _gateway_operator_methods,
    _gateway_pairing_config,
    _gateway_pairing_snapshot,
    _gateway_presence_snapshot,
    _gateway_runs_snapshot,
    _gateway_session_items,
    _gateway_ws_clients_snapshot,
    _gateway_ws_config,
    _inject_session_messages,
    _is_local_client,
    _payload_from_run_record,
    _redact_channel_config,
    _register_gateway_ws_client,
    _require_gateway_agent,
    _require_gateway_ws_auth,
    _resolve_pairing_identifier,
    _send_gateway_frame,
    _supported_channels,
    _system_channel_manager,
    _unregister_gateway_ws_client,
    _ws_response,
    router,
)
from web.state import WebServices

logger = logging.getLogger(__name__)


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
