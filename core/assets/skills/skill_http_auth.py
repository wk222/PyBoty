"""Auth/session data models and negotiation mixin for HttpSkillBackend."""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class NegotiatedToken:
    """Access token obtained via descriptor-driven auth exchange."""

    access_token: str
    token_type: str = "Bearer"
    expires_at: float = 0.0

    @property
    def expired(self) -> bool:
        return self.expires_at > 0 and time.time() >= self.expires_at


@dataclass
class RegistrySession:
    """Server-side session state for registry catalog enumeration."""

    session_id: str
    session_header: str = "X-Registry-Session"
    expires_at: float = 0.0
    initial_ttl: float = 0.0
    keepalive_path: str = ""
    cursor_mode: str = ""

    @property
    def expired(self) -> bool:
        return self.expires_at > 0 and time.time() >= self.expires_at


class HttpSkillAuthMixin:
    """Authentication and session negotiation methods for HttpSkillBackend.

    Expects the host class to provide:
    - self.headers, self.bearer_token, self.token_env_var
    - self.basic_auth_username, self.basic_auth_password, self.basic_auth_password_env
    - self.auth_header_name, self.client_id, self.client_secret, self.client_secret_env
    - self._negotiated_token_cache, self._session_cache
    - self._post_json(url, body, *, root)
    """

    def _request_headers(self: Any, root: str = "") -> dict[str, str]:
        headers = dict(self.headers)
        negotiated = self._negotiated_token_cache.get(root) if root else None
        if negotiated is not None and negotiated.expired:
            self._negotiated_token_cache.pop(root, None)
            negotiated = None
        if negotiated is not None:
            headers.setdefault(
                self.auth_header_name,
                f"{negotiated.token_type} {negotiated.access_token}",
            )
        else:
            auth_value = self._auth_header_value()
            if auth_value and self.auth_header_name not in headers:
                headers[self.auth_header_name] = auth_value
        session = self._session_cache.get(root) if root else None
        if session is not None and session.expired:
            self._session_cache.pop(root, None)
            session = None
        if session is not None:
            headers[session.session_header] = session.session_id
        return headers

    def _auth_header_value(self: Any) -> str:
        if self.bearer_token.strip():
            return f"Bearer {self.bearer_token.strip()}"
        if self.token_env_var.strip():
            env_value = os.getenv(self.token_env_var.strip(), "").strip()
            if env_value:
                return f"Bearer {env_value}"
        if self.basic_auth_username.strip():
            password = self.basic_auth_password.strip()
            if not password and self.basic_auth_password_env.strip():
                password = os.getenv(self.basic_auth_password_env.strip(), "").strip()
            if password:
                raw = f"{self.basic_auth_username.strip()}:{password}".encode()
                return f"Basic {base64.b64encode(raw).decode('ascii')}"
        return ""

    def _auth_configured(self: Any) -> bool:
        return bool(self._auth_header_value() or self.headers)

    def _auth_mode(self: Any) -> str:
        if self.bearer_token.strip() or self.token_env_var.strip():
            return "bearer"
        if self.basic_auth_username.strip():
            return "basic"
        if self.headers:
            return "custom_headers"
        return "none"

    def _negotiate_auth(self: Any, root: str, descriptor: dict[str, object] | None) -> None:
        """Exchange client credentials for an access token when the descriptor advertises a token endpoint."""
        if descriptor is None:
            return
        auth_config = descriptor.get("auth")
        if not isinstance(auth_config, dict):
            return
        token_endpoint = str(auth_config.get("token_endpoint", "")).strip()
        if not token_endpoint:
            return
        existing = self._negotiated_token_cache.get(root)
        if existing and not existing.expired:
            return
        cid = self.client_id.strip()
        secret = self.client_secret.strip()
        if not secret and self.client_secret_env.strip():
            secret = os.getenv(self.client_secret_env.strip(), "").strip()
        if not cid or not secret:
            return
        url = f"{root}/{token_endpoint.lstrip('/')}"
        try:
            payload = self._post_json(url, {"client_id": cid, "client_secret": secret})
        except Exception:
            return
        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            return
        token_type = str(payload.get("token_type", "Bearer")).strip() or "Bearer"
        expires_in = float(payload.get("expires_in", 0) or 0)
        expires_at = time.time() + expires_in if expires_in > 0 else 0.0
        self._negotiated_token_cache[root] = NegotiatedToken(
            access_token=access_token,
            token_type=token_type,
            expires_at=expires_at,
        )

    def _negotiate_session(self: Any, root: str, descriptor: dict[str, object] | None) -> None:
        """Establish a server-side session when the descriptor advertises a session endpoint."""
        if descriptor is None:
            return
        session_config = descriptor.get("session")
        if not isinstance(session_config, dict):
            return
        initiate_path = str(session_config.get("initiate_path", "")).strip()
        if not initiate_path:
            return
        existing = self._session_cache.get(root)
        if existing and not existing.expired:
            return
        url = f"{root}/{initiate_path.lstrip('/')}"
        try:
            payload = self._post_json(url, {}, root=root)
        except Exception:
            return
        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return
        session_header = str(session_config.get("session_header", "X-Registry-Session")).strip() or "X-Registry-Session"
        ttl = float(session_config.get("ttl_seconds", 0) or 0)
        expires_in = float(payload.get("expires_in", ttl) or ttl)
        expires_at = time.time() + expires_in if expires_in > 0 else 0.0
        actual_ttl = expires_in if expires_in > 0 else 0.0
        self._session_cache[root] = RegistrySession(
            session_id=session_id,
            session_header=session_header,
            expires_at=expires_at,
            initial_ttl=actual_ttl,
            keepalive_path=str(session_config.get("keepalive_path", "")).strip(),
            cursor_mode=str(session_config.get("cursor_mode", "")).strip(),
        )

    def _keepalive_session(self: Any, root: str) -> None:
        """Send a keepalive POST to extend the session TTL when past 75% of its lifetime."""
        session = self._session_cache.get(root)
        if session is None or session.expired:
            return
        if not session.keepalive_path:
            return
        if session.expires_at > 0 and session.initial_ttl > 0:
            remaining = session.expires_at - time.time()
            if remaining / session.initial_ttl > 0.25:
                return
        url = f"{root}/{session.keepalive_path.lstrip('/')}"
        try:
            payload = self._post_json(
                url,
                {"session_id": session.session_id},
                root=root,
            )
        except Exception:
            return
        new_expires_in = float(payload.get("expires_in", 0) or 0)
        if new_expires_in > 0:
            session.expires_at = time.time() + new_expires_in

    def _renegotiate_auth_if_expired(self: Any, root: str) -> bool:
        """Evict an expired token and re-negotiate using the cached descriptor."""
        existing = self._negotiated_token_cache.get(root)
        if existing is not None and not existing.expired:
            return True
        self._negotiated_token_cache.pop(root, None)
        descriptor = self._descriptor_cache.get(root)
        if descriptor is None:
            return False
        self._negotiate_auth(root, descriptor)
        fresh = self._negotiated_token_cache.get(root)
        return fresh is not None and not fresh.expired
