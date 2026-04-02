"""Gateway WS runtime, pairing registry, and presence tracking."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _new_pairing_request_id() -> str:
    return f"pair_{uuid.uuid4().hex[:12]}"


@dataclass
class GatewayPairingRequest:
    device_id: str
    role: str
    client_id: str
    request_id: str = field(default_factory=_new_pairing_request_id)
    scopes: list[str] = field(default_factory=list)
    platform: str = ""
    mode: str = ""
    user_agent: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "pending"
    note: str = ""
    decided_at: float | None = None
    decided_by: str = ""
    device_token: str = ""
    device_token_issued_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "device_id": self.device_id,
            "role": self.role,
            "client_id": self.client_id,
            "scopes": list(self.scopes),
            "platform": self.platform,
            "mode": self.mode,
            "user_agent": self.user_agent,
            "created_at": self.created_at,
            "status": self.status,
            "note": self.note,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "device_token": self.device_token,
            "device_token_issued_at": self.device_token_issued_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GatewayPairingRequest:
        return cls(
            device_id=str(payload.get("device_id", "")).strip(),
            role=str(payload.get("role", "")).strip(),
            client_id=str(payload.get("client_id", "")).strip(),
            request_id=str(payload.get("request_id", "")).strip() or _new_pairing_request_id(),
            scopes=[str(item) for item in payload.get("scopes", []) if str(item).strip()],
            platform=str(payload.get("platform", "")).strip(),
            mode=str(payload.get("mode", "")).strip(),
            user_agent=str(payload.get("user_agent", "")).strip(),
            created_at=float(payload.get("created_at", time.time())),
            status=str(payload.get("status", "pending")).strip() or "pending",
            note=str(payload.get("note", "")),
            decided_at=float(payload["decided_at"]) if payload.get("decided_at") is not None else None,
            decided_by=str(payload.get("decided_by", "")).strip(),
            device_token=str(payload.get("device_token", "")).strip(),
            device_token_issued_at=float(payload["device_token_issued_at"])
            if payload.get("device_token_issued_at") is not None
            else None,
            metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {},
        )


@dataclass
class GatewayPresenceEntry:
    connection_id: str
    device_id: str
    role: str
    scopes: list[str] = field(default_factory=list)
    client_id: str = ""
    client_version: str = ""
    platform: str = ""
    mode: str = ""
    session_key: str = ""
    connected_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    user_agent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "device_id": self.device_id,
            "role": self.role,
            "scopes": list(self.scopes),
            "client_id": self.client_id,
            "client_version": self.client_version,
            "platform": self.platform,
            "mode": self.mode,
            "session_key": self.session_key,
            "connected_at": self.connected_at,
            "last_seen_at": self.last_seen_at,
            "user_agent": self.user_agent,
            "metadata": self.metadata,
        }


@dataclass
class GatewaySessionRecord:
    session_key: str
    mode: str = ""
    thread_id: str = ""
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    last_source: str = ""
    user: str = ""
    device_ids: list[str] = field(default_factory=list)
    client_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "mode": self.mode,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "last_source": self.last_source,
            "user": self.user,
            "device_ids": list(self.device_ids),
            "client_ids": list(self.client_ids),
            "sources": list(self.sources),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GatewaySessionRecord:
        return cls(
            session_key=str(payload.get("session_key", "")).strip(),
            mode=str(payload.get("mode", "")).strip(),
            thread_id=str(payload.get("thread_id", "")).strip(),
            created_at=float(payload.get("created_at", time.time())),
            last_seen_at=float(payload.get("last_seen_at", time.time())),
            last_source=str(payload.get("last_source", "")).strip(),
            user=str(payload.get("user", "")).strip(),
            device_ids=[str(item) for item in payload.get("device_ids", []) if str(item).strip()],
            client_ids=[str(item) for item in payload.get("client_ids", []) if str(item).strip()],
            sources=[str(item) for item in payload.get("sources", []) if str(item).strip()],
            metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {},
        )


@dataclass
class GatewayRunRecord:
    run_id: str
    response_id: str
    session_key: str
    thread_id: str
    mode: str
    requested_model: str = ""
    source: str = ""
    status: str = "in_progress"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    display_input: str = ""
    output_text: str = ""
    user: str = ""
    requested_by: str = ""
    error: str = ""
    abort_requested: bool = False
    abort_requested_at: float | None = None
    aborted_by: str = ""
    abort_note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    ignored_features: list[str] = field(default_factory=list)
    client_tools: list[str] = field(default_factory=list)
    response_payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "response_id": self.response_id,
            "session_key": self.session_key,
            "thread_id": self.thread_id,
            "mode": self.mode,
            "requested_model": self.requested_model,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "display_input": self.display_input,
            "output_text": self.output_text,
            "user": self.user,
            "requested_by": self.requested_by,
            "error": self.error,
            "abort_requested": self.abort_requested,
            "abort_requested_at": self.abort_requested_at,
            "aborted_by": self.aborted_by,
            "abort_note": self.abort_note,
            "metadata": self.metadata,
            "ignored_features": list(self.ignored_features),
            "client_tools": list(self.client_tools),
            "response_payload": self.response_payload,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GatewayRunRecord:
        response_payload = payload.get("response_payload")
        return cls(
            run_id=str(payload.get("run_id", "")).strip(),
            response_id=str(payload.get("response_id", "")).strip(),
            session_key=str(payload.get("session_key", "")).strip(),
            thread_id=str(payload.get("thread_id", "")).strip(),
            mode=str(payload.get("mode", "")).strip(),
            requested_model=str(payload.get("requested_model", "")).strip(),
            source=str(payload.get("source", "")).strip(),
            status=str(payload.get("status", "in_progress")).strip() or "in_progress",
            created_at=float(payload.get("created_at", time.time())),
            updated_at=float(payload.get("updated_at", time.time())),
            completed_at=float(payload["completed_at"]) if payload.get("completed_at") is not None else None,
            display_input=str(payload.get("display_input", "")),
            output_text=str(payload.get("output_text", "")),
            user=str(payload.get("user", "")).strip(),
            requested_by=str(payload.get("requested_by", "")).strip(),
            error=str(payload.get("error", "")),
            abort_requested=bool(payload.get("abort_requested", False)),
            abort_requested_at=float(payload["abort_requested_at"])
            if payload.get("abort_requested_at") is not None
            else None,
            aborted_by=str(payload.get("aborted_by", "")).strip(),
            abort_note=str(payload.get("abort_note", "")),
            metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {},
            ignored_features=[str(item) for item in payload.get("ignored_features", []) if str(item).strip()],
            client_tools=[str(item) for item in payload.get("client_tools", []) if str(item).strip()],
            response_payload=response_payload if isinstance(response_payload, dict) else None,
        )


@dataclass
class GatewayNodeRecord:
    device_id: str
    first_seen_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    approved: bool = False
    last_connection_id: str = ""
    roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    client_ids: list[str] = field(default_factory=list)
    client_versions: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    session_keys: list[str] = field(default_factory=list)
    request_ids: list[str] = field(default_factory=list)
    user_agents: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "approved": self.approved,
            "last_connection_id": self.last_connection_id,
            "roles": list(self.roles),
            "scopes": list(self.scopes),
            "client_ids": list(self.client_ids),
            "client_versions": list(self.client_versions),
            "platforms": list(self.platforms),
            "modes": list(self.modes),
            "session_keys": list(self.session_keys),
            "request_ids": list(self.request_ids),
            "user_agents": list(self.user_agents),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GatewayNodeRecord:
        return cls(
            device_id=str(payload.get("device_id", "")).strip(),
            first_seen_at=float(payload.get("first_seen_at", time.time())),
            last_seen_at=float(payload.get("last_seen_at", time.time())),
            approved=bool(payload.get("approved", False)),
            last_connection_id=str(payload.get("last_connection_id", "")).strip(),
            roles=[str(item) for item in payload.get("roles", []) if str(item).strip()],
            scopes=[str(item) for item in payload.get("scopes", []) if str(item).strip()],
            client_ids=[str(item) for item in payload.get("client_ids", []) if str(item).strip()],
            client_versions=[str(item) for item in payload.get("client_versions", []) if str(item).strip()],
            platforms=[str(item) for item in payload.get("platforms", []) if str(item).strip()],
            modes=[str(item) for item in payload.get("modes", []) if str(item).strip()],
            session_keys=[str(item) for item in payload.get("session_keys", []) if str(item).strip()],
            request_ids=[str(item) for item in payload.get("request_ids", []) if str(item).strip()],
            user_agents=[str(item) for item in payload.get("user_agents", []) if str(item).strip()],
            metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {},
        )


class GatewayPairingRegistry:
    """Persistent store for gateway device pairing approvals."""

    def __init__(self, storage_path: str | Path):
        self._storage_path = Path(storage_path).resolve()
        self._lock = threading.RLock()
        self._approved_devices: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, GatewayPairingRequest] = {}
        self._load_unlocked()

    def is_approved(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._approved_devices

    def ensure_request(
        self,
        *,
        device_id: str,
        role: str,
        client_id: str,
        scopes: list[str],
        platform: str,
        mode: str,
        user_agent: str,
        metadata: dict[str, Any] | None = None,
    ) -> GatewayPairingRequest:
        with self._lock:
            existing = self._requests.get(device_id)
            if existing is not None and existing.status == "pending":
                return existing
            request = GatewayPairingRequest(
                device_id=device_id,
                role=role,
                client_id=client_id,
                scopes=list(scopes),
                platform=platform,
                mode=mode,
                user_agent=user_agent,
                metadata=dict(metadata or {}),
            )
            self._requests[device_id] = request
            self._persist_unlocked()
            return request

    def approve(self, device_id: str, *, note: str = "", approved_by: str = "") -> GatewayPairingRequest | None:
        with self._lock:
            request = self._resolve_request_unlocked(device_id)
            if request is None:
                return None
            request.status = "approved"
            request.note = note
            request.decided_at = time.time()
            request.decided_by = approved_by
            if not request.device_token:
                request.device_token = f"gwdt_{uuid.uuid4().hex}"
            request.device_token_issued_at = time.time()
            self._approved_devices[request.device_id] = request.to_dict()
            self._persist_unlocked()
            return request

    def reject(self, device_id: str, *, note: str = "", rejected_by: str = "") -> GatewayPairingRequest | None:
        with self._lock:
            request = self._resolve_request_unlocked(device_id)
            if request is None:
                return None
            request.status = "rejected"
            request.note = note or rejected_by
            request.decided_at = time.time()
            request.decided_by = rejected_by
            self._approved_devices.pop(request.device_id, None)
            self._persist_unlocked()
            return request

    def list_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [request.to_dict() for request in self._requests.values() if request.status == "pending"]
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def list_approved(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._approved_devices.values())
        return sorted(items, key=lambda item: item.get("decided_at", 0), reverse=True)

    def validate_device_token(self, device_id: str, token: str | None) -> bool:
        normalized_device_id = str(device_id).strip()
        normalized_token = str(token or "").strip()
        if not normalized_device_id or not normalized_token:
            return False
        with self._lock:
            approved = self._approved_devices.get(normalized_device_id)
            if not isinstance(approved, dict):
                return False
            return str(approved.get("device_token", "")).strip() == normalized_token

    def get_request(self, device_id: str) -> GatewayPairingRequest | None:
        with self._lock:
            return self._resolve_request_unlocked(device_id)

    def _resolve_request_unlocked(self, identifier: str) -> GatewayPairingRequest | None:
        request = self._requests.get(identifier)
        if request is not None:
            return request
        for candidate in self._requests.values():
            if candidate.request_id == identifier:
                return candidate
        return None

    def _load_unlocked(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except Exception:
            return
        approved = payload.get("approved_devices", {}) if isinstance(payload, dict) else {}
        requests = payload.get("requests", {}) if isinstance(payload, dict) else {}
        if isinstance(approved, dict):
            self._approved_devices = {str(key): value for key, value in approved.items() if isinstance(value, dict)}
        if isinstance(requests, dict):
            self._requests = {
                str(key): GatewayPairingRequest.from_dict(value)
                for key, value in requests.items()
                if isinstance(value, dict)
            }

    def _persist_unlocked(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "approved_devices": self._approved_devices,
            "requests": {key: request.to_dict() for key, request in self._requests.items()},
        }
        temp_path = self._storage_path.with_name(f"{self._storage_path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._storage_path)


class GatewayPresenceRegistry:
    """In-memory registry of active gateway connections."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, GatewayPresenceEntry] = {}

    def register(self, entry: GatewayPresenceEntry) -> None:
        with self._lock:
            self._entries[entry.connection_id] = entry

    def remove(self, connection_id: str) -> GatewayPresenceEntry | None:
        with self._lock:
            return self._entries.pop(connection_id, None)

    def touch(self, connection_id: str) -> GatewayPresenceEntry | None:
        with self._lock:
            entry = self._entries.get(connection_id)
            if entry is None:
                return None
            entry.last_seen_at = time.time()
            return entry

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [entry.to_dict() for entry in self._entries.values()]
        return sorted(items, key=lambda item: item["connected_at"], reverse=True)

    def by_device(self, device_id: str) -> list[dict[str, Any]]:
        with self._lock:
            items = [entry.to_dict() for entry in self._entries.values() if entry.device_id == device_id]
        return sorted(items, key=lambda item: item["connected_at"], reverse=True)

    def by_session(self, session_key: str) -> list[dict[str, Any]]:
        with self._lock:
            items = [entry.to_dict() for entry in self._entries.values() if entry.session_key == session_key]
        return sorted(items, key=lambda item: item["connected_at"], reverse=True)


class GatewaySessionRegistry:
    """Persistent registry for gateway session identity and activity."""

    def __init__(self, storage_path: str | Path):
        self._storage_path = Path(storage_path).resolve()
        self._lock = threading.RLock()
        self._sessions: dict[str, GatewaySessionRecord] = {}
        self._load_unlocked()

    def touch(
        self,
        session_key: str,
        *,
        mode: str = "",
        thread_id: str = "",
        source: str = "",
        user: str = "",
        device_id: str = "",
        client_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> GatewaySessionRecord:
        normalized_key = str(session_key).strip()
        if not normalized_key:
            raise ValueError("session_key is required")
        with self._lock:
            record = self._sessions.get(normalized_key)
            if record is None:
                record = GatewaySessionRecord(session_key=normalized_key)
                self._sessions[normalized_key] = record
            record.last_seen_at = time.time()
            if mode:
                record.mode = str(mode).strip()
            if thread_id:
                record.thread_id = str(thread_id).strip()
            if source:
                normalized_source = str(source).strip()
                record.last_source = normalized_source
                if normalized_source and normalized_source not in record.sources:
                    record.sources.append(normalized_source)
            if user:
                record.user = str(user).strip()
            if device_id:
                normalized_device_id = str(device_id).strip()
                if normalized_device_id and normalized_device_id not in record.device_ids:
                    record.device_ids.append(normalized_device_id)
            if client_id:
                normalized_client_id = str(client_id).strip()
                if normalized_client_id and normalized_client_id not in record.client_ids:
                    record.client_ids.append(normalized_client_id)
            if metadata:
                record.metadata = {**record.metadata, **metadata}
            self._persist_unlocked()
            return record

    def get(self, session_key: str) -> GatewaySessionRecord | None:
        with self._lock:
            return self._sessions.get(str(session_key).strip())

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [record.to_dict() for record in self._sessions.values()]
        return sorted(items, key=lambda item: item.get("last_seen_at", 0), reverse=True)

    def _load_unlocked(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except Exception:
            return
        sessions = payload.get("sessions", {}) if isinstance(payload, dict) else {}
        if isinstance(sessions, dict):
            self._sessions = {
                str(key): GatewaySessionRecord.from_dict(value)
                for key, value in sessions.items()
                if isinstance(value, dict)
            }

    def _persist_unlocked(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessions": {key: record.to_dict() for key, record in self._sessions.items()},
        }
        temp_path = self._storage_path.with_name(f"{self._storage_path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._storage_path)


class GatewayRunRegistry:
    """Persistent registry of gateway response/run lifecycle state."""

    _ACTIVE_STATUSES = frozenset({"in_progress", "cancelling"})

    def __init__(self, storage_path: str | Path):
        self._storage_path = Path(storage_path).resolve()
        self._lock = threading.RLock()
        self._runs: dict[str, GatewayRunRecord] = {}
        self._load_unlocked()

    def start(
        self,
        *,
        run_id: str,
        response_id: str,
        session_key: str,
        thread_id: str,
        mode: str,
        requested_model: str = "",
        source: str = "",
        display_input: str = "",
        user: str = "",
        requested_by: str = "",
        metadata: dict[str, Any] | None = None,
        ignored_features: list[str] | None = None,
        client_tools: list[str] | None = None,
    ) -> GatewayRunRecord:
        with self._lock:
            record = GatewayRunRecord(
                run_id=run_id,
                response_id=response_id or run_id,
                session_key=session_key,
                thread_id=thread_id,
                mode=mode,
                requested_model=requested_model,
                source=source,
                display_input=display_input,
                user=user,
                requested_by=requested_by,
                metadata=dict(metadata or {}),
                ignored_features=list(ignored_features or []),
                client_tools=list(client_tools or []),
            )
            self._runs[run_id] = record
            self._persist_unlocked()
            return record

    def get(self, run_id: str) -> GatewayRunRecord | None:
        with self._lock:
            return self._runs.get(str(run_id).strip())

    def get_by_response(self, response_id: str) -> GatewayRunRecord | None:
        normalized = str(response_id).strip()
        with self._lock:
            for record in self._runs.values():
                if record.response_id == normalized:
                    return record
        return None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [record.to_dict() for record in self._runs.values()]
        return sorted(items, key=lambda item: item.get("updated_at", 0), reverse=True)

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [record.to_dict() for record in self._runs.values() if record.status in self._ACTIVE_STATUSES]
        return sorted(items, key=lambda item: item.get("updated_at", 0), reverse=True)

    def latest_for_session(self, session_key: str) -> GatewayRunRecord | None:
        normalized = str(session_key).strip()
        with self._lock:
            matches = [record for record in self._runs.values() if record.session_key == normalized]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.updated_at, reverse=True)[0]

    def latest_active_for_session(self, session_key: str) -> GatewayRunRecord | None:
        normalized = str(session_key).strip()
        with self._lock:
            matches = [
                record
                for record in self._runs.values()
                if record.session_key == normalized and record.status in self._ACTIVE_STATUSES
            ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.updated_at, reverse=True)[0]

    def request_abort(self, run_id: str, *, note: str = "", requested_by: str = "") -> GatewayRunRecord | None:
        with self._lock:
            record = self._runs.get(str(run_id).strip())
            if record is None:
                return None
            record.abort_requested = True
            record.abort_requested_at = time.time()
            record.abort_note = note
            if requested_by:
                record.aborted_by = requested_by
            record.updated_at = time.time()
            if record.status == "in_progress":
                record.status = "cancelling"
            self._persist_unlocked()
            return record

    def request_abort_for_session(
        self,
        session_key: str,
        *,
        note: str = "",
        requested_by: str = "",
    ) -> GatewayRunRecord | None:
        normalized = str(session_key).strip()
        with self._lock:
            matches = [
                record
                for record in self._runs.values()
                if record.session_key == normalized and record.status in self._ACTIVE_STATUSES
            ]
            if not matches:
                return None
            record = sorted(matches, key=lambda item: item.updated_at, reverse=True)[0]
            record.abort_requested = True
            record.abort_requested_at = time.time()
            record.abort_note = note
            if requested_by:
                record.aborted_by = requested_by
            record.updated_at = time.time()
            if record.status == "in_progress":
                record.status = "cancelling"
            self._persist_unlocked()
            return record

    def is_abort_requested(self, run_id: str) -> bool:
        with self._lock:
            record = self._runs.get(str(run_id).strip())
            return bool(record.abort_requested) if record is not None else False

    def complete(
        self,
        run_id: str,
        *,
        output_text: str = "",
        response_payload: dict[str, Any] | None = None,
        status: str = "completed",
        error: str = "",
    ) -> GatewayRunRecord | None:
        with self._lock:
            record = self._runs.get(str(run_id).strip())
            if record is None:
                return None
            record.output_text = output_text
            if isinstance(response_payload, dict):
                record.response_payload = dict(response_payload)
            record.status = status
            record.error = error
            record.updated_at = time.time()
            record.completed_at = record.updated_at
            self._persist_unlocked()
            return record

    def mark_cancelled(
        self,
        run_id: str,
        *,
        note: str = "",
        cancelled_by: str = "",
        response_payload: dict[str, Any] | None = None,
    ) -> GatewayRunRecord | None:
        with self._lock:
            record = self._runs.get(str(run_id).strip())
            if record is None:
                return None
            record.abort_requested = True
            record.abort_requested_at = record.abort_requested_at or time.time()
            record.abort_note = note or record.abort_note
            if cancelled_by:
                record.aborted_by = cancelled_by
            record.status = "cancelled"
            record.updated_at = time.time()
            record.completed_at = record.updated_at
            if isinstance(response_payload, dict):
                record.response_payload = dict(response_payload)
                record.output_text = str(response_payload.get("output_text", record.output_text))
            self._persist_unlocked()
            return record

    def _load_unlocked(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except Exception:
            return
        runs = payload.get("runs", {}) if isinstance(payload, dict) else {}
        if isinstance(runs, dict):
            self._runs = {
                str(key): GatewayRunRecord.from_dict(value) for key, value in runs.items() if isinstance(value, dict)
            }

    def _persist_unlocked(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "runs": {key: record.to_dict() for key, record in self._runs.items()},
        }
        temp_path = self._storage_path.with_name(f"{self._storage_path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._storage_path)


@dataclass
class GatewayNodeCommand:
    command_id: str
    device_id: str
    command: str
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    status: str = "pending"
    requested_by: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    lease_connection_id: str = ""
    leased_at: float | None = None
    acknowledged_at: float | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "device_id": self.device_id,
            "command": self.command,
            "payload": self.payload,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "requested_by": self.requested_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "lease_connection_id": self.lease_connection_id,
            "leased_at": self.leased_at,
            "acknowledged_at": self.acknowledged_at,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GatewayNodeCommand:
        return cls(
            command_id=str(payload.get("command_id", "")).strip(),
            device_id=str(payload.get("device_id", "")).strip(),
            command=str(payload.get("command", "")).strip(),
            payload=payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {},
            idempotency_key=str(payload.get("idempotency_key", "")).strip(),
            status=str(payload.get("status", "pending")).strip() or "pending",
            requested_by=str(payload.get("requested_by", "")).strip(),
            created_at=float(payload.get("created_at", time.time())),
            updated_at=float(payload.get("updated_at", time.time())),
            lease_connection_id=str(payload.get("lease_connection_id", "")).strip(),
            leased_at=float(payload["leased_at"]) if payload.get("leased_at") is not None else None,
            acknowledged_at=float(payload["acknowledged_at"]) if payload.get("acknowledged_at") is not None else None,
            result=payload.get("result", {}) if isinstance(payload.get("result"), dict) else {},
            error=str(payload.get("error", "")),
            metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {},
        )


class GatewayNodeCommandRegistry:
    """Persistent registry for node invocation commands and pending delivery."""

    TERMINAL_STATUSES = frozenset({"completed", "failed", "rejected", "cancelled"})

    def __init__(self, storage_path: str | Path):
        self._storage_path = Path(storage_path).resolve()
        self._lock = threading.RLock()
        self._commands: dict[str, GatewayNodeCommand] = {}
        self._load_unlocked()

    def enqueue(
        self,
        *,
        device_id: str,
        command: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str = "",
        requested_by: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> GatewayNodeCommand:
        normalized_device_id = str(device_id).strip()
        normalized_command = str(command).strip()
        normalized_idempotency_key = str(idempotency_key).strip()
        if not normalized_device_id or not normalized_command:
            raise ValueError("device_id and command are required")
        with self._lock:
            if normalized_idempotency_key:
                existing = self._find_idempotent_unlocked(normalized_device_id, normalized_idempotency_key)
                if existing is not None:
                    return existing
            command_record = GatewayNodeCommand(
                command_id=f"nodecmd_{uuid.uuid4().hex[:12]}",
                device_id=normalized_device_id,
                command=normalized_command,
                payload=dict(payload or {}),
                idempotency_key=normalized_idempotency_key,
                requested_by=str(requested_by).strip(),
                metadata=dict(metadata or {}),
            )
            self._commands[command_record.command_id] = command_record
            self._persist_unlocked()
            return command_record

    def get(self, command_id: str) -> GatewayNodeCommand | None:
        with self._lock:
            return self._commands.get(str(command_id).strip())

    def list(self, *, device_id: str | None = None, include_terminal: bool = True) -> list[dict[str, Any]]:
        normalized_device_id = str(device_id).strip() if device_id is not None else ""
        with self._lock:
            items = []
            for command in self._commands.values():
                if normalized_device_id and command.device_id != normalized_device_id:
                    continue
                if not include_terminal and command.status in self.TERMINAL_STATUSES:
                    continue
                items.append(command.to_dict())
        return sorted(items, key=lambda item: item.get("updated_at", 0), reverse=True)

    def pending_for_device(self, device_id: str) -> list[dict[str, Any]]:
        normalized_device_id = str(device_id).strip()
        with self._lock:
            items = [
                command.to_dict()
                for command in self._commands.values()
                if command.device_id == normalized_device_id and command.status in {"pending", "dispatched"}
            ]
        return sorted(items, key=lambda item: item.get("created_at", 0))

    def pull(self, *, device_id: str, connection_id: str = "", limit: int = 10) -> list[GatewayNodeCommand]:
        normalized_device_id = str(device_id).strip()
        with self._lock:
            available = [
                command
                for command in self._commands.values()
                if command.device_id == normalized_device_id and command.status == "pending"
            ]
            available.sort(key=lambda item: item.created_at)
            pulled = available[: max(1, int(limit))]
            now = time.time()
            for command in pulled:
                command.status = "dispatched"
                command.lease_connection_id = str(connection_id).strip()
                command.leased_at = now
                command.updated_at = now
            if pulled:
                self._persist_unlocked()
            return list(pulled)

    def acknowledge(
        self,
        command_id: str,
        *,
        device_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> GatewayNodeCommand | None:
        normalized_status = str(status).strip().lower() or "completed"
        if normalized_status not in {"acknowledged", "completed", "failed", "rejected"}:
            raise ValueError("unsupported node command status")
        with self._lock:
            command = self._commands.get(str(command_id).strip())
            if command is None or command.device_id != str(device_id).strip():
                return None
            command.status = normalized_status
            command.acknowledged_at = time.time()
            command.updated_at = command.acknowledged_at
            command.result = dict(result or {})
            command.error = error
            self._persist_unlocked()
            return command

    def _find_idempotent_unlocked(self, device_id: str, idempotency_key: str) -> GatewayNodeCommand | None:
        for command in self._commands.values():
            if command.device_id == device_id and command.idempotency_key == idempotency_key:
                return command
        return None

    def _load_unlocked(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except Exception:
            return
        commands = payload.get("commands", {}) if isinstance(payload, dict) else {}
        if isinstance(commands, dict):
            self._commands = {
                str(key): GatewayNodeCommand.from_dict(value)
                for key, value in commands.items()
                if isinstance(value, dict)
            }

    def _persist_unlocked(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "commands": {key: command.to_dict() for key, command in self._commands.items()},
        }
        temp_path = self._storage_path.with_name(f"{self._storage_path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._storage_path)


class GatewayNodeRegistry:
    """Persistent registry of gateway device/node identities."""

    def __init__(self, storage_path: str | Path):
        self._storage_path = Path(storage_path).resolve()
        self._lock = threading.RLock()
        self._nodes: dict[str, GatewayNodeRecord] = {}
        self._load_unlocked()

    def touch_from_presence(self, entry: GatewayPresenceEntry, *, approved: bool = False) -> GatewayNodeRecord | None:
        device_id = entry.device_id.strip()
        if not device_id:
            return None
        with self._lock:
            record = self._nodes.get(device_id)
            if record is None:
                record = GatewayNodeRecord(device_id=device_id)
                self._nodes[device_id] = record
            record.last_seen_at = time.time()
            record.last_connection_id = entry.connection_id
            if approved:
                record.approved = True
            self._append_unique(record.roles, entry.role)
            for scope in entry.scopes:
                self._append_unique(record.scopes, scope)
            self._append_unique(record.client_ids, entry.client_id)
            self._append_unique(record.client_versions, entry.client_version)
            self._append_unique(record.platforms, entry.platform)
            self._append_unique(record.modes, entry.mode)
            self._append_unique(record.session_keys, entry.session_key)
            self._append_unique(record.user_agents, entry.user_agent)
            if entry.metadata:
                record.metadata = {**record.metadata, **entry.metadata}
            self._persist_unlocked()
            return record

    def record_pairing(self, request: GatewayPairingRequest) -> GatewayNodeRecord:
        with self._lock:
            record = self._nodes.get(request.device_id)
            if record is None:
                record = GatewayNodeRecord(device_id=request.device_id)
                self._nodes[request.device_id] = record
            record.last_seen_at = time.time()
            self._append_unique(record.roles, request.role)
            for scope in request.scopes:
                self._append_unique(record.scopes, scope)
            self._append_unique(record.client_ids, request.client_id)
            self._append_unique(record.platforms, request.platform)
            self._append_unique(record.modes, request.mode)
            self._append_unique(record.user_agents, request.user_agent)
            self._append_unique(record.request_ids, request.request_id)
            record.approved = request.status == "approved"
            if request.metadata:
                record.metadata = {**record.metadata, **request.metadata}
            self._persist_unlocked()
            return record

    def get(self, device_id: str) -> GatewayNodeRecord | None:
        with self._lock:
            return self._nodes.get(str(device_id).strip())

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [record.to_dict() for record in self._nodes.values()]
        return sorted(items, key=lambda item: item.get("last_seen_at", 0), reverse=True)

    def _load_unlocked(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except Exception:
            return
        nodes = payload.get("nodes", {}) if isinstance(payload, dict) else {}
        if isinstance(nodes, dict):
            self._nodes = {
                str(key): GatewayNodeRecord.from_dict(value) for key, value in nodes.items() if isinstance(value, dict)
            }

    def _persist_unlocked(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": {key: record.to_dict() for key, record in self._nodes.items()},
        }
        temp_path = self._storage_path.with_name(f"{self._storage_path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._storage_path)

    @staticmethod
    def _append_unique(items: list[str], value: str) -> None:
        normalized = str(value).strip()
        if normalized and normalized not in items:
            items.append(normalized)


class GatewayRuntime:
    """Shared gateway runtime state for REST + WebSocket surfaces."""

    def __init__(self, storage_dir: str | Path):
        storage_root = Path(storage_dir).resolve()
        self.pairings = GatewayPairingRegistry(storage_root / "gateway_pairings.json")
        self.presence = GatewayPresenceRegistry()
        self.sessions = GatewaySessionRegistry(storage_root / "gateway_sessions.json")
        self.runs = GatewayRunRegistry(storage_root / "gateway_runs.json")
        self.node_commands = GatewayNodeCommandRegistry(storage_root / "gateway_node_commands.json")
        self.nodes = GatewayNodeRegistry(storage_root / "gateway_nodes.json")

    @staticmethod
    def new_connection_id() -> str:
        return f"gw_{uuid.uuid4().hex[:12]}"
