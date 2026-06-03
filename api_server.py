from __future__ import annotations

import logging
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from core.assets.agents import AgentStorage
from core.assets.tools import ToolStorage
from core.systems.runtime import (
    DEFAULT_API_PORT,
    ProjectPaths,
    create_llm_client,
    ensure_utf8_stdio,
    get_llm_config,
    get_pybot_version,
    invoke_sub_agent,
    resolve_port,
)
from web.auth_config import debug_errors_enabled, load_api_keys_from_env
from web.gateway_guard import GatewayGuardMiddleware

ensure_utf8_stdio()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    context: str = ""
    thread_id: str = "api_default_thread"


def create_app(
    *,
    paths: ProjectPaths | None = None,
    llm_config: dict[str, Any] | None = None,
) -> FastAPI:
    resolved_paths = paths or ProjectPaths.from_root()
    resolved_paths.ensure_runtime_dirs()
    resolved_llm_config = llm_config or get_llm_config()
    default_model = str(resolved_llm_config.get("model", "gpt-4"))
    default_temperature = float(resolved_llm_config.get("temperature", 0.7))
    agent_storage = AgentStorage(base_dir=str(resolved_paths.agents_dir))
    global_tool_storage = ToolStorage(base_dir=str(resolved_paths.global_tools_dir))

    def llm_factory(model: str | None = None, temperature: float | None = None):
        return create_llm_client(
            model=model or default_model,
            temperature=default_temperature if temperature is None else temperature,
            api_key=resolved_llm_config.get("api_key"),
            base_url=resolved_llm_config.get("api_base"),
            provider=resolved_llm_config.get("provider"),
        )

    app = FastAPI(
        title="PyBot API",
        description="PyBot API surface for calling persisted agents and runtime services.",
        version=get_pybot_version(),
    )
    app.state.agent_storage = agent_storage
    app.state.llm_config = resolved_llm_config
    app.state.paths = resolved_paths

    api_keys = load_api_keys_from_env()
    app.add_middleware(
        GatewayGuardMiddleware,
        api_keys=api_keys,
        exclude_paths={"/health", "/", "/favicon.ico", "/metrics"},
    )

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, HTTPException):
            raise exc
        logger.exception("Unhandled API server error on %s %s", request.method, request.url.path)
        detail = str(exc) if debug_errors_enabled() else "Internal server error"
        return JSONResponse(status_code=500, content={"error": "internal_server_error", "detail": detail})

    @app.get("/")
    async def api_root() -> dict[str, object]:
        return {
            "name": "PyBot API",
            "status": "ok",
            "version": get_pybot_version(),
            "docs_url": "/docs",
            "openapi_url": "/openapi.json",
            "health_url": "/health",
            "agents_url": "/api/v1/agents",
        }

    @app.get("/health")
    async def health_check() -> dict[str, object]:
        return {
            "status": "ok",
            "version": get_pybot_version(),
            "service": "api",
        }

    @app.get("/favicon.ico")
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/v1/agents")
    async def list_agents() -> dict[str, object]:
        agents = agent_storage.list_agents()
        return {
            "success": True,
            "count": len(agents),
            "agents": agents,
        }

    @app.post("/api/v1/agents/{agent_name}/chat")
    async def chat_with_agent(agent_name: str, request: ChatRequest) -> dict[str, object]:
        try:
            response = invoke_sub_agent(
                agent_storage=agent_storage,
                global_tool_storage=global_tool_storage,
                llm_factory=llm_factory,
                agent_name=agent_name,
                task=request.message,
                context=request.context,
                thread_id=request.thread_id,
            )
        except ValueError as exc:
            detail = str(exc)
            status_code = 403 if "已被禁用" in detail else 404
            raise HTTPException(status_code=status_code, detail=detail) from exc
        except Exception as exc:
            logger.exception("Unhandled API chat error for agent %s", agent_name)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

        return {
            "success": True,
            "agent_name": agent_name,
            "response": response,
        }

    return app


app = create_app()


def main() -> None:
    port = resolve_port("PYBOT_API_PORT", "PORT", default=DEFAULT_API_PORT)
    print("🚀 正在启动 PyBot 超级母体 API 服务...")
    print(f"   API 地址: http://localhost:{port}")
    print(f"   文档地址: http://localhost:{port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()

