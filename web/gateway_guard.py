"""Unified Gateway Middleware for PyBoty.

Provides API key authentication, method scoping, and rate limiting
(token bucket) to protect the service endpoints.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limit configuration for a specific scope."""

    tokens_per_second: float
    burst_capacity: int


class TokenBucket:
    """Simple thread-safe token bucket for rate limiting."""

    def __init__(self, capacity: int, fill_rate: float):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = float(capacity)
        self.last_fill = time.time()

    def consume(self, tokens: int = 1) -> bool:
        now = time.time()
        elapsed = now - self.last_fill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
        self.last_fill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class GatewayGuardMiddleware(BaseHTTPMiddleware):
    """Middleware for authentication, scope validation, and rate limiting."""

    def __init__(
        self,
        app: Any,
        api_keys: dict[str, list[str]],  # key -> list of allowed scopes (e.g. ["chat", "admin"])
        rate_limits: dict[str, RateLimitConfig] | None = None,
        exclude_paths: set[str] | None = None,
        app_manager: Any = None,  # Optional AppManager reference for checking app-specific keys
    ):
        super().__init__(app)
        self.api_keys = api_keys
        self.app_manager = app_manager
        self.rate_limits = rate_limits or {
            "default": RateLimitConfig(tokens_per_second=10.0, burst_capacity=50),
            "admin": RateLimitConfig(tokens_per_second=2.0, burst_capacity=10),
        }
        if "default" not in self.rate_limits:
            self.rate_limits["default"] = RateLimitConfig(tokens_per_second=10.0, burst_capacity=50)
        self.exclude_paths = exclude_paths or {"/health", "/", "/metrics"}
        self._buckets: dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                capacity=self.rate_limits["default"].burst_capacity,
                fill_rate=self.rate_limits["default"].tokens_per_second,
            )
        )

    def _determine_scope(self, path: str) -> str:
        """Map URL path to an access scope."""
        if path.startswith("/api/admin"):
            return "admin"
        if path.startswith("/api/chat") or path.startswith("/api/gateway"):
            return "chat"
        if path.startswith("/api/workflows"):
            return "workflows"
        return "default"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Bypass excluded paths (like health checks)
        if (
            request.method == "OPTIONS"
            or path in self.exclude_paths
            or path.startswith("/static/")
            or path.startswith("/apps/")
            or path.startswith("/.well-known/")
            or path == "/favicon.ico"
        ):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        api_key = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        allowed_scopes = self.api_keys.get(api_key) if api_key else None

        # 1.5 App-specific key check
        # If the request is for an app, check if the key belongs to that app
        if self.app_manager is not None:
            app_name = None
            if path.startswith("/apps/"):
                parts = path.split("/")
                if len(parts) > 2:
                    app_name = parts[2]
            elif path.startswith("/api/apps/"):
                parts = path.split("/")
                if len(parts) > 3:
                    app_name = parts[3]
            
            if app_name:
                app_def = self.app_manager.get_app(app_name)
                if app_def:
                    if not app_def.require_auth:
                        # Public app, allow access to its endpoints without token
                        allowed_scopes = ["*"]
                    elif allowed_scopes is None and api_key in app_def.api_keys:
                        # Authenticated app with valid app-specific key
                        allowed_scopes = ["*"]

        # 1. Authentication (Standard Check)
        if allowed_scopes is None:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Missing or invalid Authorization header"},
            )

        if allowed_scopes is None:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Invalid API key"},
            )

        # 2. Scope Validation
        required_scope = self._determine_scope(path)
        if "*" not in allowed_scopes and required_scope not in allowed_scopes and required_scope != "default":
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "detail": f"API key lacks required scope: {required_scope}"},
            )

        # 3. Rate Limiting
        # We rate limit by API key + Scope
        bucket_key = f"{api_key}:{required_scope}"
        if bucket_key not in self._buckets:
            # Initialize specific bucket if configured, else fallback to default
            limit_cfg = self.rate_limits.get(required_scope, self.rate_limits["default"])
            self._buckets[bucket_key] = TokenBucket(
                capacity=limit_cfg.burst_capacity,
                fill_rate=limit_cfg.tokens_per_second,
            )

        bucket = self._buckets[bucket_key]
        if not bucket.consume(1):
            logger.warning("Rate limit exceeded for key %s... on scope %s", api_key[:4], required_scope)
            return JSONResponse(
                status_code=429,
                content={"error": "too_many_requests", "detail": "Rate limit exceeded"},
                headers={"Retry-After": "1"},
            )

        # Pass to the next middleware/router
        return await call_next(request)
