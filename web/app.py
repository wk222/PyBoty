"""FastAPI app factory for the PyBot service."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.entrypoints import DEFAULT_WEB_PORT, ensure_utf8_stdio, resolve_port
from core.project_paths import ProjectPaths
from core.version import get_pybot_version
from web.routers import admin, apps, chat, workflows, workspace
from web.state import WebServices

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

    app.include_router(chat.router)
    app.include_router(admin.router)
    app.include_router(workspace.router)
    app.include_router(apps.router)
    app.include_router(workflows.router)

    @app.get("/")
    async def serve_index():
        index_path = Path(static_dir / "index.html")
        if index_path.exists():
            return FileResponse(index_path)
        return {"message": "static/index.html not found"}

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
