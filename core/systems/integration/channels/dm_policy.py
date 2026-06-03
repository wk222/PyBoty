"""Inbound DM access control — inspired by OpenClaw dmPolicy / allowFrom."""

from __future__ import annotations

import json
import secrets
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .channel_runtime import ChannelConfig


_PAIRING_ALPHABET = string.ascii_uppercase + string.digits


@dataclass(frozen=True)
class DmAccessDecision:
    allowed: bool
    reason: str = ""
    pairing_code: str = ""
    reply_message: str = ""


def resolve_dm_policy(config: ChannelConfig | None) -> str:
    if config is None:
        return "open"
    policy = str(getattr(config, "dm_policy", None) or config.extra.get("dmPolicy") or "open").strip().lower()
    if policy not in {"open", "allowlist", "pairing"}:
        return "open"
    return policy


def resolve_allow_from(config: ChannelConfig | None) -> set[str]:
    if config is None:
        return set()
    allow_from = getattr(config, "allow_from", None)
    if allow_from:
        return {str(item).strip() for item in allow_from if str(item).strip()}
    raw = config.extra.get("allowFrom")
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


class ChannelPairingStore:
    """File-backed pending pairing codes for IM channels."""

    def __init__(self, store_path: Path | str) -> None:
        self._path = Path(store_path).resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"pending": {}, "approved": {}}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"pending": {}, "approved": {}}
        if not isinstance(payload, dict):
            return {"pending": {}, "approved": {}}
        payload.setdefault("pending", {})
        payload.setdefault("approved", {})
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_approved(self, channel_name: str, user_id: str) -> bool:
        data = self._load()
        approved = data.get("approved", {})
        channel = approved.get(channel_name, {})
        return bool(isinstance(channel, dict) and user_id in channel)

    def approve(self, channel_name: str, user_id: str) -> None:
        data = self._load()
        approved = data.setdefault("approved", {})
        channel = approved.setdefault(channel_name, {})
        if isinstance(channel, dict):
            channel[user_id] = {"user_id": user_id}
        self._save(data)

    def request_pairing(self, channel_name: str, user_id: str) -> str:
        data = self._load()
        pending = data.setdefault("pending", {})
        channel_pending = pending.setdefault(channel_name, {})
        if not isinstance(channel_pending, dict):
            channel_pending = {}
            pending[channel_name] = channel_pending
        existing = channel_pending.get(user_id)
        if isinstance(existing, dict) and existing.get("code"):
            return str(existing["code"])
        code = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(8))
        channel_pending[user_id] = {"user_id": user_id, "code": code}
        self._save(data)
        return code

    def confirm_pairing(self, channel_name: str, code: str) -> str | None:
        data = self._load()
        pending = data.get("pending", {})
        channel_pending = pending.get(channel_name, {})
        if not isinstance(channel_pending, dict):
            return None
        for user_id, payload in list(channel_pending.items()):
            if isinstance(payload, dict) and str(payload.get("code", "")).upper() == code.upper():
                approved = data.setdefault("approved", {})
                channel_approved = approved.setdefault(channel_name, {})
                if isinstance(channel_approved, dict):
                    channel_approved[str(user_id)] = {"user_id": str(user_id)}
                channel_pending.pop(user_id, None)
                self._save(data)
                return str(user_id)
        return None

    def list_pending(self, channel_name: str | None = None) -> list[dict[str, str]]:
        data = self._load()
        pending = data.get("pending", {})
        rows: list[dict[str, str]] = []
        if not isinstance(pending, dict):
            return rows
        channels = [channel_name] if channel_name else list(pending.keys())
        for name in channels:
            channel_pending = pending.get(name, {})
            if not isinstance(channel_pending, dict):
                continue
            for user_id, payload in channel_pending.items():
                if isinstance(payload, dict):
                    rows.append(
                        {
                            "channel": str(name),
                            "user_id": str(user_id),
                            "code": str(payload.get("code", "")),
                        }
                    )
        return rows


def evaluate_dm_access(
    *,
    channel_name: str,
    user_id: str,
    config: ChannelConfig | None,
    pairing_store: ChannelPairingStore | None,
) -> DmAccessDecision:
    policy = resolve_dm_policy(config)
    user_id = str(user_id).strip()
    if not user_id:
        return DmAccessDecision(allowed=False, reason="missing_user_id")

    if policy == "open":
        return DmAccessDecision(allowed=True)

    allow_from = resolve_allow_from(config)
    if user_id in allow_from:
        return DmAccessDecision(allowed=True)

    if pairing_store is not None and pairing_store.is_approved(channel_name, user_id):
        return DmAccessDecision(allowed=True)

    if policy == "allowlist":
        return DmAccessDecision(
            allowed=False,
            reason="not_in_allowlist",
            reply_message=(
                f"Access denied: user `{user_id}` is not in allow_from for channel `{channel_name}`. "
                "Ask a team admin to add your ID."
            ),
        )

    if policy == "pairing":
        store = pairing_store or ChannelPairingStore(Path("workspace") / ".runtime" / "channel_pairing.json")
        code = store.request_pairing(channel_name, user_id)
        return DmAccessDecision(
            allowed=False,
            reason="pairing_required",
            pairing_code=code,
            reply_message=(
                f"Pairing required. Your code: {code}\n"
                f"Ask a team admin to approve: POST /api/channels/pairing/approve "
                f"with channel={channel_name}, user_id={user_id}, code={code}"
            ),
        )

    return DmAccessDecision(allowed=True)
