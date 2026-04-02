from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.gateway_guard import GatewayGuardMiddleware, RateLimitConfig


def test_gateway_guard_authentication():
    app = FastAPI()

    @app.get("/api/chat/message")
    def chat_endpoint():
        return {"status": "ok"}

    app.add_middleware(
        GatewayGuardMiddleware,
        api_keys={"valid-key": ["chat"]},
    )

    client = TestClient(app)

    # Missing auth
    response = client.get("/api/chat/message")
    assert response.status_code == 401

    # Invalid auth
    response = client.get("/api/chat/message", headers={"Authorization": "Bearer wrong-key"})
    assert response.status_code == 401

    # Valid auth
    response = client.get("/api/chat/message", headers={"Authorization": "Bearer valid-key"})
    assert response.status_code == 200


def test_gateway_guard_method_scopes():
    app = FastAPI()

    @app.get("/api/admin/config")
    def admin_endpoint():
        return {"status": "ok"}

    @app.get("/api/chat/message")
    def chat_endpoint():
        return {"status": "ok"}

    app.add_middleware(
        GatewayGuardMiddleware,
        api_keys={
            "admin-key": ["*"],
            "chat-key": ["chat"],
        },
    )

    client = TestClient(app)

    # Admin key can access both
    assert client.get("/api/admin/config", headers={"Authorization": "Bearer admin-key"}).status_code == 200
    assert client.get("/api/chat/message", headers={"Authorization": "Bearer admin-key"}).status_code == 200

    # Chat key can access chat but not admin
    assert client.get("/api/chat/message", headers={"Authorization": "Bearer chat-key"}).status_code == 200
    assert client.get("/api/admin/config", headers={"Authorization": "Bearer chat-key"}).status_code == 403


def test_gateway_guard_rate_limiting():
    app = FastAPI()

    @app.get("/api/chat/fast")
    def fast_endpoint():
        return {"status": "ok"}

    app.add_middleware(
        GatewayGuardMiddleware,
        api_keys={"test-key": ["chat"]},
        rate_limits={
            "chat": RateLimitConfig(tokens_per_second=2.0, burst_capacity=3),
        },
    )

    client = TestClient(app)
    headers = {"Authorization": "Bearer test-key"}

    # Consume burst capacity
    assert client.get("/api/chat/fast", headers=headers).status_code == 200
    assert client.get("/api/chat/fast", headers=headers).status_code == 200
    assert client.get("/api/chat/fast", headers=headers).status_code == 200

    # Next request should be rate limited
    assert client.get("/api/chat/fast", headers=headers).status_code == 429

    # Wait for token refill (0.5s should refill 1 token)
    time.sleep(0.6)
    assert client.get("/api/chat/fast", headers=headers).status_code == 200


def test_gateway_guard_exclude_paths():
    app = FastAPI()

    @app.get("/health")
    def health_endpoint():
        return {"status": "healthy"}

    app.add_middleware(
        GatewayGuardMiddleware,
        api_keys={"valid-key": ["*"]},
        exclude_paths={"/health"},
    )

    client = TestClient(app)

    # Health check should pass without auth
    response = client.get("/health")
    assert response.status_code == 200
