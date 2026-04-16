"""Chat and conversation APIs."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal  # noqa: F401

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.modes.canvas import get_canvas_profile, list_canvas_profiles
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
    session_key = services.session_runtime.session_key_for_thread(thread_id) or ""

    def event_generator():
        final_content = None
        for event in agent.chat_stream(request.message):
            evt_type = event.get("type")
            if evt_type in {"step", "schedule", "done", "error"}:
                if evt_type in {"done", "error"}:
                    final_content = event.get("content", "")
                if evt_type == "done" and session_key:
                    event = {**event, "session_key": session_key}
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
            # Deep Digest: 异步归纳本次对话（依据 ExecutionCanvas 策略）
            try:
                history = services.conversations.get_history(thread_id)
                canvas_name = services.conversations.get_canvas(thread_id)
                canvas_profile = get_canvas_profile(canvas_name)
                if services.memory_distill and len(history) >= 4:
                    services.memory_distill.journal_async(history[-20:])
                    should_distill = canvas_profile.digest_on_every_turn or (
                        canvas_profile.digest_interval > 0
                        and len(history) % canvas_profile.digest_interval == 0
                    )
                    if should_distill:
                        services.memory_distill.distill_async()
            except Exception as _dd_e:
                logger.debug("MemoryDistill trigger error: %s", _dd_e)

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


class CanvasRequest(BaseModel):
    canvas: Literal["focused", "balanced", "deep"]


@router.get("/api/canvas/profiles")
async def list_canvases() -> dict[str, Any]:
    """List all available execution canvas profiles."""
    return {"canvases": list_canvas_profiles()}


@router.get("/api/conversations/{thread_id}/canvas")
async def get_canvas(
    thread_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    """Get the execution canvas for a conversation."""
    canvas = services.conversations.get_canvas(thread_id)
    profile = get_canvas_profile(canvas)
    return {"thread_id": thread_id, **profile.to_dict()}


@router.put("/api/conversations/{thread_id}/canvas")
async def set_canvas(
    thread_id: str,
    req: CanvasRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    """Switch execution canvas for a conversation.

    Clears the cached agent so next request picks up new capability profile.
    """
    canvas = req.canvas
    try:
        services.conversations.set_canvas(thread_id, canvas)
        services.agents.remove(thread_id)
        logger.info("Canvas set to %s for thread %s", canvas, thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    profile = get_canvas_profile(canvas)
    return {"thread_id": thread_id, **profile.to_dict()}


@router.get("/api/conversations/{thread_id}/trace")
async def get_trace(
    thread_id: str,
    limit: int = 100,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, Any]:
    from core.systems.runtime.event_bus import event_bus
    
    events = event_bus.persistent_history(limit=limit, session_id=thread_id)
    return {
        "thread_id": thread_id,
        "events": [
            {
                "id": i,
                "type": e.type.value,
                "source": e.source,
                "payload": e.payload,
                "timestamp": e.timestamp,
            }
            for i, e in enumerate(events)
        ]
    }


@router.get("/api/trace/global")
async def get_global_trace(
    limit: int = 200,
    event_type: str | None = None,
) -> dict[str, Any]:
    """Return global event trace across all sessions, with optional type filter."""
    from core.systems.runtime.event_bus import event_bus

    all_events = event_bus.persistent_history(limit=limit)

    if event_type:
        all_events = [e for e in all_events if e.type.value == event_type]

    type_counts: dict[str, int] = {}
    for e in all_events:
        type_counts[e.type.value] = type_counts.get(e.type.value, 0) + 1

    return {
        "events": [
            {
                "id": i,
                "type": e.type.value,
                "source": e.source,
                "session_id": e.session_id,
                "payload": e.payload,
                "timestamp": e.timestamp,
            }
            for i, e in enumerate(all_events)
        ],
        "type_counts": type_counts,
        "total": len(all_events),
    }


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


@router.get("/api/cost_stats")
def get_cost_stats(services: WebServices = SERVICES_DEPENDENCY):
    """Return aggregated LLM and tool cost statistics."""
    tracker = getattr(services, "cost_tracker", None)
    if tracker is None:
        return {
            "total_llm_calls": 0,
            "total_tool_calls": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "model_breakdown": {},
            "tool_breakdown": {},
        }
    summary = tracker.get_summary()
    return summary.to_dict()


class CliCommandRequest(BaseModel):
    command: str


@router.post("/api/cli/execute")
async def execute_cli_command(
    req: CliCommandRequest,
    services: WebServices = SERVICES_DEPENDENCY,
):
    """Execute a CLI-style command from the web terminal.

    Supports slash commands and direct prompts.
    """
    cmd = req.command.strip()
    if not cmd:
        return {"output": "", "type": "empty"}

    if cmd.startswith("/"):
        return _handle_slash_command(cmd, services)

    try:
        agent = services.agent_pool.get_or_create("default-cli")
        bot = agent._require_bot() if hasattr(agent, "_require_bot") else None
        if bot:
            response = bot.invoke(cmd)
        else:
            response = agent.invoke(cmd) if hasattr(agent, "invoke") else f"Agent pool returned: {type(agent)}"
        return {"output": str(response), "type": "response"}
    except Exception as exc:
        return {"output": f"Error: {exc}", "type": "error"}


def _handle_slash_command(cmd: str, services: WebServices) -> dict:
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command == "/help":
        help_text = """Available commands:
  /help          - Show this help message
  /status        - System status overview
  /mode          - Show current mode
  /canvas        - Show current canvas profile
  /cost          - Show token cost summary
  /tools         - List available tools
  /skills        - List installed skills
  /agents        - List registered agents
  /workflows     - List saved workflows
  /memory        - Show memory stats
  /clear         - Clear terminal
  /version       - Show PyBot version"""
        return {"output": help_text, "type": "help"}

    if command == "/status":
        try:
            from core.systems.runtime import get_pybot_version
            version = get_pybot_version()
            tracker = getattr(services, "cost_tracker", None)
            cost_info = ""
            if tracker:
                s = tracker.get_summary()
                cost_info = f"\nCost: ${s.total_cost_usd:.4f} ({s.total_tokens} tokens, {s.total_llm_calls} calls)"
            return {"output": f"PyBot {version}\nStatus: Running{cost_info}", "type": "info"}
        except Exception as exc:
            return {"output": f"Status check failed: {exc}", "type": "error"}

    if command == "/mode":
        try:
            modes = build_system_summary()
            current = modes.get("current", {})
            return {"output": f"Current mode: {current.get('name', 'unknown')}\nLabel: {current.get('label', '-')}", "type": "info"}
        except Exception:
            return {"output": "Mode: assistant (default)", "type": "info"}

    if command == "/canvas":
        profiles = list_canvas_profiles()
        lines = ["Available canvas profiles (+ auto mode):"]
        for p in profiles:
            lines.append(f"  {p['name']:10s}  temp={p.get('llm', {}).get('temperature', '?')}  tokens={p.get('llm', {}).get('max_tokens', '?')}")
        lines.append("  auto        Automatically selects canvas based on prompt complexity")
        return {"output": "\n".join(lines), "type": "info"}

    if command == "/cost":
        tracker = getattr(services, "cost_tracker", None)
        if not tracker:
            return {"output": "Cost tracking not initialized.", "type": "info"}
        s = tracker.get_summary()
        return {
            "output": f"Total cost: ${s.total_cost_usd:.4f}\nTokens: {s.total_tokens:,}\nLLM calls: {s.total_llm_calls}\nTool calls: {s.total_tool_calls}",
            "type": "info",
        }

    if command == "/tools":
        try:
            agent = services.agent_pool.get_or_create("default-cli")
            tools = agent.list_tools() if hasattr(agent, "list_tools") else {}
            if not tools:
                return {"output": "No custom tools registered.", "type": "info"}
            lines = [f"  {name}" for name in sorted(tools.keys()) if isinstance(tools, dict)]
            return {"output": f"Registered tools ({len(lines)}):\n" + "\n".join(lines), "type": "info"}
        except Exception as exc:
            return {"output": f"Error listing tools: {exc}", "type": "error"}

    if command == "/version":
        try:
            from core.systems.runtime import get_pybot_version
            return {"output": f"PyBot {get_pybot_version()}", "type": "info"}
        except Exception:
            return {"output": "PyBot v5.1", "type": "info"}

    if command == "/clear":
        return {"output": "", "type": "clear"}

    if command in ("/skills", "/agents", "/workflows", "/memory"):
        return {"output": f"Command {command} — use the dedicated UI page for detailed views.", "type": "info"}

    return {"output": f"Unknown command: {command}\nType /help for available commands.", "type": "error"}


@router.get("/api/conversations/{thread_id}/context-budget")
def get_context_budget(
    thread_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
):
    """Return context window token distribution for visualization."""
    from core.modes.canvas import get_canvas_profile

    conv = services.conversation_store.get(thread_id) if hasattr(services, "conversation_store") else None
    canvas_name = "balanced"
    if conv and hasattr(conv, "canvas"):
        canvas_name = conv.canvas or "balanced"

    profile = get_canvas_profile(canvas_name)
    max_tokens = profile.get("llm_max_tokens", 8192) if isinstance(profile, dict) else 8192

    messages = []
    if conv and hasattr(conv, "messages"):
        messages = conv.messages or []

    msg_tokens = sum(len(str(m.get("content", ""))) // 4 for m in messages) if messages else 0

    system_prompt_est = 800
    memory_est = 0
    tool_defs_est = 0

    try:
        mem_facade = getattr(services, "memory_facade", None)
        if mem_facade:
            ctx = mem_facade.get_context_prompt(canvas_name, "")
            memory_est = len(ctx) // 4
    except Exception:
        pass

    try:
        agent = services.agent_pool.get_or_create(thread_id)
        tools = agent.list_tools() if hasattr(agent, "list_tools") else {}
        tool_defs_est = len(tools) * 120
    except Exception:
        pass

    used = system_prompt_est + memory_est + tool_defs_est + msg_tokens
    available = max(0, max_tokens - used)

    categories = [
        {"name": "System Prompt", "tokens": system_prompt_est, "color": "#818cf8"},
        {"name": "Memory", "tokens": memory_est, "color": "#34d399"},
        {"name": "Tool Definitions", "tokens": tool_defs_est, "color": "#fbbf24"},
        {"name": "Conversation", "tokens": msg_tokens, "color": "#60a5fa"},
        {"name": "Available", "tokens": available, "color": "#374151"},
    ]

    return {
        "canvas": canvas_name,
        "max_tokens": max_tokens,
        "used_tokens": used,
        "available_tokens": available,
        "percentage": round(used / max_tokens * 100, 1) if max_tokens > 0 else 0,
        "categories": categories,
        "message_count": len(messages),
    }


@router.get("/api/conversations/{thread_id}/export")
def export_conversation(
    thread_id: str,
    fmt: str = "markdown",
    services: WebServices = SERVICES_DEPENDENCY,
):
    """Export a conversation as Markdown or JSON."""
    conv = services.conversation_store.get(thread_id) if hasattr(services, "conversation_store") else None
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = conv.messages if hasattr(conv, "messages") else []
    title = conv.title if hasattr(conv, "title") else thread_id
    canvas = conv.canvas if hasattr(conv, "canvas") else "balanced"

    if fmt == "json":
        return {
            "thread_id": thread_id,
            "title": title,
            "canvas": canvas,
            "message_count": len(messages),
            "messages": [
                {
                    "role": m.get("role", "unknown"),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", ""),
                }
                for m in messages
            ],
        }

    lines = [
        f"# {title}",
        f"",
        f"> Thread: `{thread_id}` | Canvas: `{canvas}` | Messages: {len(messages)}",
        f"",
        "---",
        "",
    ]
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        ts = m.get("timestamp", "")
        if role == "user":
            lines.append(f"## User {f'({ts})' if ts else ''}")
        elif role == "assistant":
            lines.append(f"## Assistant {f'({ts})' if ts else ''}")
        else:
            lines.append(f"## {role} {f'({ts})' if ts else ''}")
        lines.append("")
        lines.append(content)
        lines.append("")
        lines.append("---")
        lines.append("")

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(
        content="\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{thread_id}.md"'},
    )
