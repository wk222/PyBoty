"""Runtime helpers for inbound/outbound channel processing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChannelConfig:
    """Configuration for a concrete channel adapter."""

    name: str
    kind: str
    enabled: bool = True
    token: str | None = None
    app_id: str | None = None
    app_secret: str | None = None
    corp_id: str | None = None
    agent_id: str | None = None
    secret: str | None = None
    encoding_aes_key: str | None = None
    api_base: str | None = None
    reply_mode: str = "passive"
    dm_policy: str = "open"
    allow_from: tuple[str, ...] = field(default_factory=tuple)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any] | None, *, default_kind: str | None = None) -> ChannelConfig:
        payload = data if isinstance(data, dict) else {}
        known = {
            "enabled",
            "token",
            "app_id",
            "app_secret",
            "corp_id",
            "agent_id",
            "secret",
            "encoding_aes_key",
            "api_base",
            "reply_mode",
            "kind",
            "dm_policy",
            "dmPolicy",
            "allow_from",
            "allowFrom",
        }
        raw_allow = payload.get("allow_from", payload.get("allowFrom", []))
        if isinstance(raw_allow, str):
            allow_from = tuple(item.strip() for item in raw_allow.split(",") if item.strip())
        elif isinstance(raw_allow, list):
            allow_from = tuple(str(item).strip() for item in raw_allow if str(item).strip())
        else:
            allow_from = ()
        dm_policy = str(payload.get("dm_policy") or payload.get("dmPolicy") or "open").strip().lower()
        if dm_policy not in {"open", "allowlist", "pairing"}:
            dm_policy = "open"
        return cls(
            name=name,
            kind=str(payload.get("kind") or default_kind or name),
            enabled=bool(payload.get("enabled", True)),
            token=payload.get("token"),
            app_id=payload.get("app_id"),
            app_secret=payload.get("app_secret"),
            corp_id=payload.get("corp_id"),
            agent_id=str(payload.get("agent_id")) if payload.get("agent_id") is not None else None,
            secret=payload.get("secret"),
            encoding_aes_key=payload.get("encoding_aes_key"),
            api_base=payload.get("api_base"),
            reply_mode=str(payload.get("reply_mode", "passive")),
            dm_policy=dm_policy,
            allow_from=allow_from,
            extra={key: value for key, value in payload.items() if key not in known},
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self.extra.get(key, default)


@dataclass(frozen=True)
class ChannelWebhookRequest:
    """Normalized inbound webhook request."""

    method: str
    query_params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    raw_body: bytes = b""
    json_payload: dict[str, Any] | None = None
    content_type: str = ""

    @property
    def text_body(self) -> str:
        return self.raw_body.decode("utf-8", errors="ignore")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ChannelWebhookRequest:
        return cls(method="POST", json_payload=payload, content_type="application/json")


@dataclass(frozen=True)
class ChannelWebhookVerification:
    """Webhook verification result."""

    verified: bool
    challenge: str | None = None
    content_type: str = "text/plain"
    error: str | None = None


@dataclass(frozen=True)
class ChannelSendResult:
    """Normalized outbound delivery result."""

    success: bool
    response_body: str | None = None
    content_type: str = "text/plain"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, *, metadata: dict[str, Any] | None = None) -> ChannelSendResult:
        return cls(success=True, metadata=metadata or {})

    @classmethod
    def reply(
        cls,
        body: str,
        *,
        content_type: str = "text/plain",
        metadata: dict[str, Any] | None = None,
    ) -> ChannelSendResult:
        return cls(success=True, response_body=body, content_type=content_type, metadata=metadata or {})

    @classmethod
    def failed(cls, error: str, *, metadata: dict[str, Any] | None = None) -> ChannelSendResult:
        return cls(success=False, error=error, metadata=metadata or {})


@dataclass(frozen=True)
class ChannelMessage:
    """Normalized message received from an external channel."""

    user_id: str
    message: str
    thread_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> ChannelMessage | None:
        if not isinstance(payload, dict):
            return None
        user_id = str(payload.get("user_id", "")).strip()
        message = str(payload.get("message", "")).strip()
        thread_id = str(payload.get("thread_id", "")).strip()
        if not user_id or not message or not thread_id:
            return None
        metadata = payload.get("metadata", {})
        return cls(
            user_id=user_id,
            message=message,
            thread_id=thread_id,
            metadata=metadata if isinstance(metadata, dict) else {},
        )


@dataclass(frozen=True)
class ChannelRouteRule:
    """Declarative route for inbound channel traffic."""

    name: str
    channel: str = ""
    enabled: bool = True
    target: str = "agent"
    mode: str = "assistant"
    workflow_name: str = ""
    thread_template: str = "{thread_id}"
    contains: str = ""
    starts_with: str = ""
    user_pattern: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any], index: int) -> ChannelRouteRule:
        known = {
            "name",
            "channel",
            "enabled",
            "target",
            "mode",
            "workflow_name",
            "thread_template",
            "contains",
            "starts_with",
            "user_pattern",
        }
        return cls(
            name=str(payload.get("name", f"route_{index}")).strip() or f"route_{index}",
            channel=str(payload.get("channel", "")).strip(),
            enabled=bool(payload.get("enabled", True)),
            target=str(payload.get("target", "agent")).strip() or "agent",
            mode=str(payload.get("mode", "assistant")).strip() or "assistant",
            workflow_name=str(payload.get("workflow_name", "")).strip(),
            thread_template=str(payload.get("thread_template", "{thread_id}")).strip() or "{thread_id}",
            contains=str(payload.get("contains", "")).strip(),
            starts_with=str(payload.get("starts_with", "")).strip(),
            user_pattern=str(payload.get("user_pattern", "")).strip(),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    def matches(self, *, channel_name: str, message: ChannelMessage) -> bool:
        if not self.enabled:
            return False
        if self.channel and self.channel != channel_name:
            return False
        text = message.message
        if self.contains and self.contains not in text:
            return False
        if self.starts_with and not text.startswith(self.starts_with):
            return False
        if self.user_pattern:
            try:
                if re.search(self.user_pattern, message.user_id) is None:
                    return False
            except re.error:
                return False
        return True

    def render_thread_id(self, channel_name: str, message: ChannelMessage) -> str:
        """Render a deterministic thread ID based on the route template and message metadata."""
        # Extract group/chat identifiers from channel-specific metadata if present
        chat_id = (
            message.metadata.get("chat_id")
            or message.metadata.get("conversation_id")
            or message.metadata.get("group_id")
            or ""
        )
        values = {
            "channel": channel_name,
            "user_id": message.user_id,
            "thread_id": message.thread_id,
            "message": message.message,
            "chat_id": chat_id,
        }
        rendered = self.thread_template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered.strip() or message.thread_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "channel": self.channel,
            "enabled": self.enabled,
            "target": self.target,
            "mode": self.mode,
            "workflow_name": self.workflow_name,
            "thread_template": self.thread_template,
            "contains": self.contains,
            "starts_with": self.starts_with,
            "user_pattern": self.user_pattern,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ChannelRouteDecision:
    """Resolved routing decision for a channel message."""

    target: str = "agent"
    mode: str = "assistant"
    thread_id: str = ""
    workflow_name: str = ""
    rule_name: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "mode": self.mode,
            "thread_id": self.thread_id,
            "workflow_name": self.workflow_name,
            "rule_name": self.rule_name,
            "metadata": self.metadata,
        }


class ChannelRouter:
    """Simple first-match router for inbound channel messages."""

    def __init__(self, routes: list[dict[str, Any]] | None = None) -> None:
        self._rules = [ChannelRouteRule.from_dict(payload, index) for index, payload in enumerate(routes or [])]

    def list_rules(self) -> list[dict[str, Any]]:
        return [rule.to_dict() for rule in self._rules]

    def resolve(self, channel_name: str, message: ChannelMessage) -> ChannelRouteDecision:
        for rule in self._rules:
            if rule.matches(channel_name=channel_name, message=message):
                return ChannelRouteDecision(
                    target=rule.target,
                    mode=rule.mode,
                    workflow_name=rule.workflow_name,
                    thread_id=rule.render_thread_id(channel_name, message),
                    rule_name=rule.name,
                    metadata=dict(rule.metadata),
                )
        return ChannelRouteDecision(
            target="agent",
            mode="assistant",
            thread_id=message.thread_id,
            rule_name="default",
            metadata={"fallback": True},
        )

    def preview(self, channel_name: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        message = ChannelMessage.from_payload(payload)
        if message is None:
            return {
                "matched": False,
                "error": "Invalid payload format",
            }
        decision = self.resolve(channel_name, message)
        return {
            "matched": True,
            "message": {
                "user_id": message.user_id,
                "message": message.message,
                "thread_id": message.thread_id,
                "metadata": message.metadata,
            },
            "decision": decision.to_dict(),
        }

    @staticmethod
    def _render_thread_id(template: str, channel_name: str, message: ChannelMessage) -> str:
        # Deprecated: render_thread_id has moved to ChannelRouteRule
        return message.thread_id


class ChannelRuntime:
    """Process normalized channel messages via the configured agent callback."""

    def __init__(
        self,
        routes: list[dict[str, Any]] | None = None,
        *,
        workspace_dir: str | Path | None = None,
    ) -> None:
        from .dm_policy import ChannelPairingStore, evaluate_dm_access

        self._agent_callback: Any = None
        self._route_callback: Any = None
        self._router = ChannelRouter(routes)
        self._workspace_dir = Path(workspace_dir or "workspace")
        self._pairing_store = ChannelPairingStore(self._workspace_dir / ".runtime" / "channel_pairing.json")
        self._evaluate_dm_access = evaluate_dm_access

    def set_agent_callback(self, callback: Any) -> None:
        self._agent_callback = callback

    def set_route_callback(self, callback: Any) -> None:
        self._route_callback = callback

    def list_routes(self) -> list[dict[str, Any]]:
        return self._router.list_rules()

    def preview_route(self, channel_name: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        return self._router.preview(channel_name, payload)

    def list_pairings(self, channel_name: str | None = None) -> list[dict[str, str]]:
        return self._pairing_store.list_pending(channel_name)

    def approve_pairing(self, channel_name: str, code: str) -> str | None:
        return self._pairing_store.confirm_pairing(channel_name, code)

    def _enforce_dm_access(self, channel_name: str, channel: Any, parsed: ChannelMessage) -> dict[str, Any] | None:
        config = getattr(channel, "config", None)
        decision = self._evaluate_dm_access(
            channel_name=channel_name,
            user_id=parsed.user_id,
            config=config,
            pairing_store=self._pairing_store,
        )
        if decision.allowed:
            return None
        if decision.reply_message:
            self._normalize_send_result(channel.send_message(parsed.user_id, decision.reply_message))
        return {
            "success": False,
            "error": decision.reason,
            "pairing_code": decision.pairing_code,
            "thread_id": parsed.thread_id,
        }

    def handle_incoming(
        self,
        *,
        channel_name: str,
        channel: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        parsed = ChannelMessage.from_payload(channel.parse_incoming(payload))
        if parsed is None:
            return {"success": False, "error": "Invalid payload format"}
        blocked = self._enforce_dm_access(channel_name, channel, parsed)
        if blocked is not None:
            return blocked
        decision = self._router.resolve(channel_name, parsed)
        if self._route_callback is None and self._agent_callback is None:
            return {"success": False, "error": "Agent callback not configured"}

        print(f"[ChannelManager] 收到 {channel_name} 消息: {parsed.message} (User: {parsed.user_id})")
        try:
            response = (
                self._route_callback(channel_name, parsed, decision)
                if self._route_callback is not None
                else self._agent_callback(parsed.message, parsed.thread_id)
            )
            delivery = self._normalize_send_result(channel.send_message(parsed.user_id, response))
            if not delivery.success:
                return {
                    "success": False,
                    "error": delivery.error or "Channel send failed",
                    "thread_id": parsed.thread_id,
                    "route": decision.to_dict(),
                }
            return {
                "success": True,
                "status": "processed",
                "thread_id": parsed.thread_id,
                "response": response,
                "route": decision.to_dict(),
            }
        except Exception as exc:
            error_msg = f"处理失败: {exc}"
            self._normalize_send_result(channel.send_message(parsed.user_id, error_msg))
            return {"success": False, "error": error_msg, "thread_id": parsed.thread_id, "route": decision.to_dict()}

    async def handle_webhook(
        self,
        *,
        channel_name: str,
        channel: Any,
        request: ChannelWebhookRequest,
    ) -> dict[str, Any]:
        verification = channel.verify_webhook(request)
        if verification is not None:
            if not verification.verified:
                return {"success": False, "error": verification.error or "Webhook verification failed"}
            if request.method.upper() == "GET":
                return {
                    "success": True,
                    "status": "verified",
                    "http_response": {
                        "body": verification.challenge or "",
                        "content_type": verification.content_type,
                    },
                }

        parsed = ChannelMessage.from_payload(channel.parse_incoming(request))
        if parsed is None:
            return {"success": False, "error": "Invalid payload format"}
        blocked = self._enforce_dm_access(channel_name, channel, parsed)
        if blocked is not None:
            return blocked
        decision = self._router.resolve(channel_name, parsed)
        if self._route_callback is None and self._agent_callback is None:
            return {"success": False, "error": "Agent callback not configured"}

        print(f"[ChannelManager] 收到 {channel_name} 消息: {parsed.message} (User: {parsed.user_id})")
        try:
            response = (
                self._route_callback(channel_name, parsed, decision)
                if self._route_callback is not None
                else self._agent_callback(parsed.message, parsed.thread_id)
            )
            delivery = self._normalize_send_result(
                await channel.async_send_message(parsed.user_id, response, message_context=parsed)
            )
            if not delivery.success:
                return {
                    "success": False,
                    "error": delivery.error or "Channel send failed",
                    "thread_id": parsed.thread_id,
                    "route": decision.to_dict(),
                }
            result: dict[str, Any] = {
                "success": True,
                "status": "processed",
                "thread_id": parsed.thread_id,
                "response": response,
                "route": decision.to_dict(),
            }
            if delivery.response_body is not None:
                result["http_response"] = {
                    "body": delivery.response_body,
                    "content_type": delivery.content_type,
                }
            return result
        except Exception as exc:
            error_msg = f"处理失败: {exc}"
            self._normalize_send_result(
                await channel.async_send_message(parsed.user_id, error_msg, message_context=parsed)
            )
            return {"success": False, "error": error_msg, "thread_id": parsed.thread_id, "route": decision.to_dict()}

    @staticmethod
    def _normalize_send_result(result: Any) -> ChannelSendResult:
        if isinstance(result, ChannelSendResult):
            return result
        if isinstance(result, bool):
            return ChannelSendResult.ok() if result else ChannelSendResult.failed("Channel send failed")
        if result is None:
            return ChannelSendResult.ok()
        return ChannelSendResult.ok(metadata={"raw_result": result})
