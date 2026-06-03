"""Gateway responses endpoints (`/v1/responses`).

Split out of :mod:`web.routers.gateway` to keep individual modules under the
1500-line ceiling. Importing this module registers the three endpoints
(``create``, ``get``, ``cancel``) on the shared ``router``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.systems.integration.openresponses import (
    OpenResponsesRequest,
    PreparedGatewayRequest,
    build_openresponses_created_event,
    build_openresponses_payload,
    build_openresponses_tool_call_payload,
    parse_bearer_token,
    prepare_gateway_request,
    request_has_function_call_output,
    select_client_tool_name,
)
from web.dependencies import get_services
from web.routers.gateway import (
    _broadcast_gateway_event,
    _cancelled_run_payload,
    _channel_route_callback,
    _ensure_gateway_channel_routing,
    _gateway_endpoint_enabled,
    _gateway_session_items,
    _gateway_tool_call_arguments,
    _inject_session_messages,
    _payload_from_run_record,
    _prepared_from_run_record,
    _require_gateway_agent,
    _require_gateway_auth,
    _resolve_gateway_request_context,
    _stringify_channel_route_result,
    _sync_session_runtime,
    _sse,
    router,
)
from web.state import WebServices

logger = logging.getLogger(__name__)
SERVICES_DEPENDENCY = Depends(get_services)


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
