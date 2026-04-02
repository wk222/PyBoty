"""Chat and conversation APIs."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.modes.system_model import build_system_summary
from core.systems.runtime import get_pybot_version
from web.dependencies import get_services
from web.state import WebServices

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])
SERVICES_DEPENDENCY = Depends(get_services)


class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default-7x24"


class CreateConversationRequest(BaseModel):
    title: str | None = None


@router.get("/api/health")
async def health(
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    return {
        "status": "running",
        "timestamp": time.time(),
        "version": get_pybot_version(),
        "llm_configured": services.llm_configured,
        "system_summary": build_system_summary(),
    }


@router.get("/api/conversations")
async def list_conversations(
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, list[dict]]:
    return {"conversations": services.conversations.list_conversations()}


@router.post("/api/conversations")
async def create_conversation(
    req: CreateConversationRequest | None = None,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, str]:
    created = services.conversations.create_conversation(req.title if req else None)
    session = services.session_runtime.bind_conversation(
        thread_id=created["thread_id"],
        title=created["title"],
        root_mode="assistant",
        source="chat.create",
    )
    return {
        **created,
        "session_key": str(session["session_key"]),
        "mode": str(session["primary_mode"]),
    }


@router.delete("/api/conversations/{thread_id}")
async def delete_conversation(
    thread_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, bool]:
    services.conversations.delete_conversation(thread_id)
    services.session_runtime.delete_session_for_thread(thread_id)
    services.agents.remove(thread_id)
    return {"success": True}


@router.get("/api/conversations/{thread_id}/history")
async def get_history(
    thread_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    return {
        "thread_id": thread_id,
        "messages": services.conversations.get_history(thread_id),
        "session": services.session_runtime.get_session_for_thread(thread_id),
    }


@router.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> StreamingResponse:
    thread_id = request.thread_id
    services.conversations.ensure_conversation(thread_id, title_hint=request.message)
    services.conversations.append_message(thread_id, "user", request.message, title_hint=request.message)
    services.session_runtime.record_message(
        thread_id=thread_id,
        role="user",
        content=request.message,
        root_mode="assistant",
        source="chat.stream",
    )

    agent = services.agents.get_or_create(thread_id)

    def event_generator():
        final_content = None
        for event in agent.chat_stream(request.message):
            evt_type = event.get("type")
            if evt_type in {"step", "schedule", "done", "error"}:
                if evt_type in {"done", "error"}:
                    final_content = event.get("content", "")
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        if final_content:
            services.conversations.append_message(thread_id, "assistant", final_content)
            services.session_runtime.record_message(
                thread_id=thread_id,
                role="assistant",
                content=final_content,
                root_mode="assistant",
                source="chat.stream",
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/chat")
async def chat(
    request: ChatRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        thread_id = request.thread_id
        services.conversations.ensure_conversation(thread_id, title_hint=request.message)
        services.conversations.append_message(thread_id, "user", request.message, title_hint=request.message)
        services.session_runtime.record_message(
            thread_id=thread_id,
            role="user",
            content=request.message,
            root_mode="assistant",
            source="chat.sync",
        )

        agent = services.agents.get_or_create(thread_id)
        response = agent.chat(request.message)
        services.conversations.append_message(thread_id, "assistant", response)
        session = services.session_runtime.record_message(
            thread_id=thread_id,
            role="assistant",
            content=response,
            root_mode="assistant",
            source="chat.sync",
        )

        return {
            "thread_id": thread_id,
            "session_key": session["session_key"],
            "response": response,
            "agents_active": list(agent.list_agents().keys()),
            "tools_active": list(agent.list_tools().keys()),
        }
    except Exception as exc:
        logger.exception("Chat request failed for thread %s", request.thread_id)
        raise HTTPException(status_code=500, detail="处理对话请求时发生内部错误") from exc


@router.get("/api/status/{thread_id}")
async def get_status(
    thread_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    agent = services.agents.get(thread_id)
    if agent is None:
        return {
            "thread_id": thread_id,
            "session": services.session_runtime.get_session_for_thread(thread_id),
            "agents": {},
            "tools": {},
            "usage_stats": {},
        }
    return {
        "thread_id": thread_id,
        "session": services.session_runtime.get_session_for_thread(thread_id),
        "agents": agent.list_agents(),
        "tools": agent.list_tools(),
        "usage_stats": agent.get_tool_usage_stats(),
    }
