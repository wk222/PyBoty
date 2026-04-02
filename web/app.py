"""FastAPI app factory for the PyBot service."""

from __future__ import annotations

import logging
import os
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from core.entrypoints import DEFAULT_WEB_PORT, ensure_utf8_stdio, resolve_port
from core.systems.runtime import ProjectPaths, get_pybot_version
from core.systems.runtime.event_bus import Event, EventType, event_bus
from web.gateway_guard import GatewayGuardMiddleware
from web.routers import admin, apps, chat, gateway, sessions, workflows, workspace
from web.state import WebServices

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_CORS_ORIGINS = (
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    services: WebServices = app.state.services
    services.startup()
    try:
        yield
    finally:
        services.shutdown()


def _load_cors_settings() -> tuple[list[str], bool]:
    raw_origins = os.environ.get("PYBOT_CORS_ORIGINS", "")
    if raw_origins.strip():
        origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    else:
        origins = list(_DEFAULT_CORS_ORIGINS)

    allow_credentials = "*" not in origins
    return origins, allow_credentials


def create_app(
    *,
    paths: ProjectPaths | None = None,
    llm_config: dict[str, object] | None = None,
    control_config: dict[str, object] | None = None,
) -> FastAPI:
    services = WebServices.create(paths=paths, llm_config=llm_config, control_config=control_config)
    static_dir = services.paths.root_dir / "static"
    static_dir.mkdir(exist_ok=True)
    cors_origins, allow_credentials = _load_cors_settings()

    app = FastAPI(title="PyBot 7x24 Service", version=get_pybot_version(), lifespan=lifespan)
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Load API keys from environment or config
    # Format: PYBOT_API_KEYS="key1:admin,chat;key2:chat"
    api_keys_config = os.environ.get("PYBOT_API_KEYS", "")
    api_keys = {}
    if api_keys_config:
        for pair in api_keys_config.split(";"):
            if ":" in pair:
                k, scopes = pair.split(":", 1)
                api_keys[k.strip()] = [s.strip() for s in scopes.split(",")]
    else:
        # Default dev key if none provided (for backward compatibility)
        api_keys["dev-key"] = ["*"]

    app.add_middleware(
        GatewayGuardMiddleware,
        api_keys=api_keys,
        exclude_paths={"/health", "/", "/metrics", "/openapi.json", "/docs"},
        app_manager=services.app_manager,
    )

    app.include_router(chat.router)
    app.include_router(sessions.router)
    app.include_router(gateway.router)
    app.include_router(admin.router)
    app.include_router(workspace.router)
    app.include_router(apps.router)
    app.include_router(workflows.router)

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled error on %s %s: %s", request.method, request.url.path, exc)
        logger.debug(traceback.format_exc())

        event_bus.emit(
            Event(
                type=EventType.ERROR,
                source=f"API:{request.method}:{request.url.path}",
                payload={"error": str(exc), "traceback": traceback.format_exc()},
            )
        )

        return JSONResponse(
            status_code=500,
            content={"error": "internal_server_error", "detail": str(exc)},
        )

    @app.get("/")
    async def serve_index():
        index_path = Path(static_dir / "index.html")
        if index_path.exists():
            return FileResponse(index_path)
        return {"message": "static/index.html not found"}

    @app.get("/health")
    async def health_check():
        return {
            "status": "ok",
            "version": get_pybot_version(),
        }

    @app.get("/favicon.ico")
    async def favicon():
        favicon_path = Path(static_dir / "favicon.ico")
        if favicon_path.exists():
            return FileResponse(favicon_path)
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


def main() -> None:
    ensure_utf8_stdio()
    port = resolve_port("PYBOT_WEB_PORT", "PORT", default=DEFAULT_WEB_PORT)
    app = create_app()
    print("🚀 PyBot 7x24 服务启动中...")
    print(f"   前端地址: http://localhost:{port}")
    print(f"   API 文档: http://localhost:{port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
