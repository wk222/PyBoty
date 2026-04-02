"""OpenResponses-style request/response helpers for the PyBot gateway surface."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from core.modes import resolve_mode_profile

_MODEL_PREFIXES = ("pybot:", "agent:", "openclaw:")
_SAFE_SESSION_CHARS = re.compile(r"[^a-zA-Z0-9._-]+")


class OpenResponsesRequest(BaseModel):
    """Small OpenResponses-compatible request model.

    The real upstream schema is much larger; this intentionally implements the
    useful subset we can translate cleanly onto the current PyBot runtime.
    """

    model: str = "pybot:assistant"
    input: str | list[Any]
    instructions: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    stream: bool = False
    user: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    max_output_tokens: int | None = None
    max_tool_calls: int | None = None
    previous_response_id: str | None = None
    store: bool | None = None
    truncation: str | None = None
    reasoning: dict[str, Any] | None = None


@dataclass(slots=True)
class PreparedGatewayRequest:
    response_id: str
    requested_model: str
    resolved_mode: str
    session_key: str
    thread_id: str
    prompt: str
    display_input: str
    ignored_features: list[str]
    client_tools: list[str]
    metadata: dict[str, Any]


def parse_bearer_token(header_value: str | None) -> str | None:
    if not header_value:
        return None
    prefix = "bearer "
    lowered = header_value.strip().lower()
    if not lowered.startswith(prefix):
        return None
    return header_value.strip()[len(prefix) :].strip()


def resolve_gateway_mode(*, model: str | None, header_agent_id: str | None = None) -> str:
    candidate = (header_agent_id or model or "").strip()
    if not candidate:
        return "assistant"

    lowered = candidate.lower()
    for prefix in _MODEL_PREFIXES:
        if lowered.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    return resolve_mode_profile(candidate or "assistant").name


def build_gateway_models_catalog() -> list[dict[str, Any]]:
    created = int(time.time())
    models: list[dict[str, Any]] = []
    for mode_name in ("assistant", "app_matrix", "admin"):
        profile = resolve_mode_profile(mode_name)
        models.append(
            {
                "id": f"pybot:{mode_name}",
                "object": "model",
                "created": created,
                "owned_by": "pybot",
                "mode": mode_name,
                "label": profile.label,
                "capabilities": profile.enabled_capabilities(),
                "aliases": [f"agent:{mode_name}", f"openclaw:{mode_name}"],
            }
        )
    return models


def parse_gateway_thread_id(thread_id: str) -> dict[str, str] | None:
    normalized = str(thread_id).strip()
    for mode_name in ("assistant", "app_matrix", "admin"):
        prefix = f"gateway-{mode_name}-"
        if normalized.startswith(prefix):
            return {
                "mode": mode_name,
                "session_key": normalized[len(prefix) :],
                "thread_id": normalized,
            }
    return None


def prepare_gateway_request(
    request: OpenResponsesRequest,
    *,
    session_key: str | None = None,
    header_agent_id: str | None = None,
) -> PreparedGatewayRequest:
    ignored_features: list[str] = []
    client_tools = _extract_client_tool_names(request.tools, ignored_features)
    resolved_mode = resolve_gateway_mode(model=request.model, header_agent_id=header_agent_id)
    resolved_session_key = _build_session_key(explicit=session_key, user=request.user)
    response_id = f"resp_{uuid.uuid4().hex}"
    thread_id = _build_thread_id(resolved_mode, resolved_session_key)

    transcript = _flatten_input(request.input, ignored_features=ignored_features)
    prompt_sections: list[str] = []
    if request.instructions:
        prompt_sections.append(f"Operator instructions:\n{request.instructions.strip()}")
    if client_tools:
        ignored_features.append("client_function_tools")
        prompt_sections.append(
            "Client-declared tools are provided as compatibility hints only in this gateway surface.\n"
            "Declared tool names: " + ", ".join(client_tools)
        )
    if request.max_output_tokens is not None:
        ignored_features.append("max_output_tokens")
    if request.max_tool_calls is not None:
        ignored_features.append("max_tool_calls")
    if request.previous_response_id:
        ignored_features.append("previous_response_id")
    if request.store is not None:
        ignored_features.append("store")
    if request.truncation:
        ignored_features.append("truncation")
    if request.reasoning:
        ignored_features.append("reasoning")
    if request.tool_choice is not None:
        ignored_features.append("tool_choice")

    display_input = transcript.strip() or "(empty input)"
    prompt_sections.append(display_input)

    return PreparedGatewayRequest(
        response_id=response_id,
        requested_model=request.model,
        resolved_mode=resolved_mode,
        session_key=resolved_session_key,
        thread_id=thread_id,
        prompt="\n\n".join(section for section in prompt_sections if section.strip()),
        display_input=display_input,
        ignored_features=_unique(ignored_features),
        client_tools=client_tools,
        metadata=dict(request.metadata or {}),
    )


def build_openresponses_payload(
    *,
    prepared: PreparedGatewayRequest,
    output_text: str,
    status: str = "completed",
) -> dict[str, Any]:
    created_at = int(time.time())
    return {
        "id": prepared.response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "model": prepared.requested_model,
        "output": [
            {
                "id": f"msg_{uuid.uuid4().hex[:12]}",
                "type": "message",
                "status": "completed" if status == "completed" else status,
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": output_text,
                        "annotations": [],
                    }
                ],
            }
        ],
        "output_text": output_text,
        "parallel_tool_calls": False,
        "tool_choice": "none",
        "usage": None,
        "metadata": prepared.metadata,
        "pybot": {
            "run_id": prepared.response_id,
            "mode": prepared.resolved_mode,
            "session_key": prepared.session_key,
            "thread_id": prepared.thread_id,
            "ignored_features": prepared.ignored_features,
            "client_tools": prepared.client_tools,
        },
    }


def build_openresponses_created_event(prepared: PreparedGatewayRequest) -> dict[str, Any]:
    return {
        "id": prepared.response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "in_progress",
        "model": prepared.requested_model,
        "output": [],
        "output_text": "",
        "parallel_tool_calls": False,
        "tool_choice": "none",
        "usage": None,
        "metadata": prepared.metadata,
        "pybot": {
            "run_id": prepared.response_id,
            "mode": prepared.resolved_mode,
            "session_key": prepared.session_key,
            "thread_id": prepared.thread_id,
            "ignored_features": prepared.ignored_features,
            "client_tools": prepared.client_tools,
        },
    }


def request_has_function_call_output(request: OpenResponsesRequest) -> bool:
    return _has_function_call_output(request.input)


def select_client_tool_name(request: OpenResponsesRequest, prepared: PreparedGatewayRequest) -> str | None:
    if not prepared.client_tools:
        return None
    tool_choice = request.tool_choice
    if isinstance(tool_choice, str):
        normalized = tool_choice.strip().lower()
        if normalized in {"none", ""}:
            return None
        if normalized in {"required", "auto"}:
            return prepared.client_tools[0]
        return tool_choice.strip()
    if isinstance(tool_choice, dict):
        if isinstance(tool_choice.get("function"), dict):
            name = str(tool_choice["function"].get("name", "")).strip()
            if name:
                return name
        name = str(tool_choice.get("name", "")).strip()
        if name:
            return name
    return prepared.client_tools[0]


def build_openresponses_tool_call_payload(
    *,
    prepared: PreparedGatewayRequest,
    tool_name: str,
    arguments: dict[str, Any],
    call_id: str | None = None,
) -> dict[str, Any]:
    created_at = int(time.time())
    resolved_call_id = call_id or f"call_{uuid.uuid4().hex[:12]}"
    function_item = {
        "id": f"fc_{uuid.uuid4().hex[:12]}",
        "type": "function_call",
        "call_id": resolved_call_id,
        "name": tool_name,
        "arguments": arguments,
        "status": "completed",
    }
    return {
        "id": prepared.response_id,
        "object": "response",
        "created_at": created_at,
        "status": "incomplete",
        "model": prepared.requested_model,
        "output": [function_item],
        "output_text": "",
        "parallel_tool_calls": False,
        "tool_choice": "required",
        "usage": None,
        "metadata": prepared.metadata,
        "required_action": {
            "type": "submit_tool_outputs",
            "submit_tool_outputs": {
                "tool_calls": [function_item],
            },
        },
        "pybot": {
            "run_id": prepared.response_id,
            "mode": prepared.resolved_mode,
            "session_key": prepared.session_key,
            "thread_id": prepared.thread_id,
            "ignored_features": prepared.ignored_features,
            "client_tools": prepared.client_tools,
            "compatibility_mode": "client_function_bridge",
        },
    }


def _flatten_input(input_value: str | list[Any], *, ignored_features: list[str]) -> str:
    if isinstance(input_value, str):
        return input_value.strip()

    transcript_lines: list[str] = []
    for raw_item in input_value:
        if isinstance(raw_item, str):
            text = raw_item.strip()
            if text:
                transcript_lines.append(f"USER: {text}")
            continue
        if not isinstance(raw_item, dict):
            continue

        item_type = str(raw_item.get("type", "message")).strip() or "message"
        if item_type == "message":
            role = str(raw_item.get("role", "user")).strip() or "user"
            content = _flatten_content(raw_item.get("content"), ignored_features=ignored_features)
            if content:
                transcript_lines.append(f"{role.upper()}: {content}")
            continue
        if item_type == "function_call_output":
            output = _flatten_content(raw_item.get("output"), ignored_features=ignored_features)
            call_id = str(raw_item.get("call_id", "")).strip()
            label = f"TOOL[{call_id}]" if call_id else "TOOL"
            if output:
                transcript_lines.append(f"{label}: {output}")
            continue
        if item_type in {"reasoning", "item_reference"}:
            ignored_features.append(item_type)
            continue

        content = _flatten_content(raw_item, ignored_features=ignored_features)
        if content:
            transcript_lines.append(f"{item_type.upper()}: {content}")

    return "\n".join(line for line in transcript_lines if line.strip())


def _flatten_content(content: Any, *, ignored_features: list[str]) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, (int, float, bool)):
        return str(content)
    if isinstance(content, list):
        parts = [_flatten_content(item, ignored_features=ignored_features) for item in content]
        return "\n".join(part for part in parts if part)
    if not isinstance(content, dict):
        return str(content).strip()

    item_type = str(content.get("type", "")).strip()
    if item_type in {"input_text", "output_text", "text"}:
        return str(content.get("text", "")).strip()
    if item_type == "input_image":
        ignored_features.append("input_image")
        return "[Image input omitted by gateway compatibility layer]"
    if item_type == "input_file":
        ignored_features.append("input_file")
        filename = str(content.get("filename", "") or content.get("name", "")).strip()
        suffix = f": {filename}" if filename else ""
        return f"[File input omitted by gateway compatibility layer{suffix}]"

    if "text" in content:
        return str(content.get("text", "")).strip()
    if "content" in content:
        return _flatten_content(content.get("content"), ignored_features=ignored_features)
    if "output" in content:
        return _flatten_content(content.get("output"), ignored_features=ignored_features)
    return str(content).strip()


def _extract_client_tool_names(
    tools: list[dict[str, Any]] | None,
    ignored_features: list[str],
) -> list[str]:
    if not tools:
        return []
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("type", "")).strip() == "function" and isinstance(tool.get("function"), dict):
            name = str(tool["function"].get("name", "")).strip()
        else:
            name = str(tool.get("name", "")).strip()
        if name:
            names.append(name)
    if tools and not names:
        ignored_features.append("client_function_tools")
    return names


def _has_function_call_output(input_value: str | list[Any]) -> bool:
    if isinstance(input_value, str):
        return False
    for raw_item in input_value:
        if isinstance(raw_item, dict):
            item_type = str(raw_item.get("type", "")).strip()
            if item_type == "function_call_output":
                return True
            content = raw_item.get("content")
            if isinstance(content, list) and _has_function_call_output(content):
                return True
    return False


def _build_session_key(*, explicit: str | None, user: str | None) -> str:
    if explicit and explicit.strip():
        return _sanitize_session_key(explicit.strip())
    if user and user.strip():
        digest = hashlib.sha1(user.strip().encode("utf-8")).hexdigest()[:16]
        return f"user-{digest}"
    return f"ephemeral-{uuid.uuid4().hex[:16]}"


def _sanitize_session_key(value: str) -> str:
    sanitized = _SAFE_SESSION_CHARS.sub("-", value).strip("-.")
    if sanitized:
        return sanitized[:96]
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"session-{digest}"


def _build_thread_id(mode: str, session_key: str) -> str:
    return _sanitize_session_key(f"gateway-{mode}-{session_key}")


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
