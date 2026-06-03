"""Model Context Protocol (MCP) Hub — bridge external MCP servers to PyBot.

Implements the MCP client side:
  - stdio transport: spawn subprocess, communicate via stdin/stdout JSON-RPC 2.0
  - Dynamic tool discovery via tools/list
  - Tool invocation via tools/call
  - Resource listing via resources/list (optional)
  - Server lifecycle management (start, stop, restart, health check)

Config: workspace/mcp_servers.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"
    url: str | None = None
    enabled: bool = True


@dataclass
class MCPToolDescriptor:
    """Parsed MCP tool descriptor from tools/list response."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPResourceDescriptor:
    """Parsed MCP resource descriptor."""

    uri: str
    name: str
    description: str = ""
    mime_type: str | None = None
    server_name: str = ""


class MCPServerConnection:
    """Manages a single MCP server subprocess connection."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._tools: list[MCPToolDescriptor] = []
        self._resources: list[MCPResourceDescriptor] = []
        self._initialized = False

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """Start the MCP server subprocess."""
        if self.is_running:
            return True

        cmd = [self.config.command] + self.config.args
        env = {**os.environ, **self.config.env}

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
            )
            logger.info("MCP server %r started (pid=%s)", self.config.name, self._process.pid)
        except FileNotFoundError:
            logger.error("MCP server %r: command not found: %s", self.config.name, self.config.command)
            return False
        except Exception as exc:
            logger.error("MCP server %r failed to start: %s", self.config.name, exc)
            return False

        try:
            self._initialize()
            self._discover_tools()
            self._discover_resources()
            self._initialized = True
            return True
        except Exception as exc:
            logger.error("MCP server %r initialization failed: %s", self.config.name, exc)
            self.stop()
            return False

    def stop(self) -> None:
        """Stop the MCP server subprocess."""
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception:
                pass
            logger.info("MCP server %r stopped", self.config.name)
            self._process = None
            self._initialized = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request and return the response."""
        if not self.is_running:
            raise ConnectionError(f"MCP server {self.config.name!r} is not running")

        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            request["params"] = params

        with self._lock:
            return self._send_and_receive(request)

    def _send_and_receive(self, request: dict[str, Any]) -> dict[str, Any]:
        """Wire-level send/receive for JSON-RPC over stdio."""
        proc = self._process
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise ConnectionError("No process available")

        payload = json.dumps(request) + "\n"
        try:
            proc.stdin.write(payload.encode())
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ConnectionError(f"Failed to send to MCP server: {exc}") from exc

        deadline = time.monotonic() + _REQUEST_TIMEOUT
        while time.monotonic() < deadline:
            try:
                line = proc.stdout.readline()
            except Exception as exc:
                raise ConnectionError(f"Failed to read from MCP server: {exc}") from exc

            if not line:
                if proc.poll() is not None:
                    raise ConnectionError("MCP server process exited unexpectedly")
                continue

            line = line.strip()
            if not line:
                continue

            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue

            if isinstance(response, dict) and response.get("id") == request.get("id"):
                if "error" in response:
                    err = response["error"]
                    raise RuntimeError(f"MCP error {err.get('code', '?')}: {err.get('message', 'unknown')}")
                return response.get("result", {})

        raise TimeoutError(f"MCP server {self.config.name!r} did not respond within {_REQUEST_TIMEOUT}s")

    def _initialize(self) -> None:
        """Send MCP initialize handshake."""
        result = self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}},
                "clientInfo": {"name": "pybot", "version": "0.1.0"},
            },
        )
        server_info = result.get("serverInfo", {})
        logger.info(
            "MCP %r initialized: %s v%s",
            self.config.name,
            server_info.get("name", "?"),
            server_info.get("version", "?"),
        )

        try:
            self._send_request("notifications/initialized")
        except Exception:
            pass

    def _discover_tools(self) -> None:
        """Discover available tools via tools/list."""
        try:
            result = self._send_request("tools/list")
        except Exception as exc:
            logger.warning("MCP %r tools/list failed: %s", self.config.name, exc)
            return

        raw_tools = result.get("tools", [])
        self._tools = [
            MCPToolDescriptor(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=self.config.name,
            )
            for t in raw_tools
            if t.get("name")
        ]
        logger.info("MCP %r: discovered %d tools", self.config.name, len(self._tools))

    def _discover_resources(self) -> None:
        """Discover available resources via resources/list."""
        try:
            result = self._send_request("resources/list")
        except Exception:
            return

        raw = result.get("resources", [])
        self._resources = [
            MCPResourceDescriptor(
                uri=r.get("uri", ""),
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mimeType"),
                server_name=self.config.name,
            )
            for r in raw
            if r.get("uri")
        ]
        logger.info("MCP %r: discovered %d resources", self.config.name, len(self._resources))

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Invoke a tool on this MCP server."""
        result = self._send_request("tools/call", {"name": tool_name, "arguments": arguments})
        content = result.get("content", [])
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)

    def read_resource(self, uri: str) -> str:
        """Read a resource from this MCP server."""
        result = self._send_request("resources/read", {"uri": uri})
        contents = result.get("contents", [])
        texts = []
        for item in contents:
            if isinstance(item, dict):
                texts.append(item.get("text", item.get("blob", "")))
        return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)

    @property
    def tools(self) -> list[MCPToolDescriptor]:
        return list(self._tools)

    @property
    def resources(self) -> list[MCPResourceDescriptor]:
        return list(self._resources)


class MCPToolAdapter(BaseTool):
    """Wrap an MCP tool as a LangChain BaseTool."""

    name: str = ""
    description: str = ""
    server_name: str = ""
    tool_name: str = ""
    _hub: Any = None

    def __init__(self, *, hub: MCPHub, descriptor: MCPToolDescriptor, **kwargs):
        qualified_name = f"mcp_{descriptor.server_name}_{descriptor.name}"
        desc = descriptor.description or f"MCP tool '{descriptor.name}' from server '{descriptor.server_name}'"
        super().__init__(
            name=qualified_name,
            description=desc,
            server_name=descriptor.server_name,
            tool_name=descriptor.name,
            **kwargs,
        )
        self._hub = hub

    def _run(self, **kwargs: Any) -> str:
        return self._hub.call_tool_sync(self.server_name, self.tool_name, kwargs)


class MCPHub:
    """Central hub managing multiple MCP server connections."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.config_path = os.path.join(workspace_dir, "mcp_servers.json")
        self._connections: dict[str, MCPServerConnection] = {}
        self._tools_cache: list[BaseTool] = []
        self._configs: dict[str, MCPServerConfig] = {}
        self._load_config()

    def _load_config(self) -> None:
        if not os.path.exists(self.config_path):
            default_config = {
                "mcpServers": {
                    "example_sqlite": {
                        "command": "uvx",
                        "args": ["mcp-server-sqlite", "--db-path", "workspace/data/agent.db"],
                        "enabled": False,
                    }
                }
            }
            os.makedirs(os.path.dirname(self.config_path) or ".", exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            self._configs = {}
            return

        try:
            with open(self.config_path, encoding="utf-8") as f:
                data = json.load(f)
            servers = data.get("mcpServers", {})
            for name, cfg in servers.items():
                self._configs[name] = MCPServerConfig(
                    name=name,
                    command=cfg.get("command", ""),
                    args=cfg.get("args", []),
                    env=cfg.get("env", {}),
                    transport=cfg.get("transport", "stdio"),
                    url=cfg.get("url"),
                    enabled=cfg.get("enabled", True),
                )
        except Exception as exc:
            logger.error("Failed to load MCP config: %s", exc)

    def start_server(self, name: str) -> bool:
        """Start a specific MCP server by name."""
        config = self._configs.get(name)
        if not config:
            logger.error("MCP server %r not found in config", name)
            return False
        if not config.enabled:
            logger.info("MCP server %r is disabled, skipping", name)
            return False

        conn = MCPServerConnection(config)
        if conn.start():
            self._connections[name] = conn
            self._tools_cache = []
            return True
        return False

    def start_all(self) -> dict[str, bool]:
        """Start all enabled MCP servers."""
        results = {}
        for name, config in self._configs.items():
            if config.enabled:
                results[name] = self.start_server(name)
            else:
                results[name] = False
        return results

    def stop_server(self, name: str) -> None:
        conn = self._connections.pop(name, None)
        if conn:
            conn.stop()
            self._tools_cache = []

    def stop_all(self) -> None:
        for name in list(self._connections):
            self.stop_server(name)

    def restart_server(self, name: str) -> bool:
        self.stop_server(name)
        return self.start_server(name)

    def get_server_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all configured servers."""
        status = {}
        for name, config in self._configs.items():
            conn = self._connections.get(name)
            status[name] = {
                "configured": True,
                "enabled": config.enabled,
                "running": conn.is_running if conn else False,
                "tools_count": len(conn.tools) if conn else 0,
                "resources_count": len(conn.resources) if conn else 0,
                "transport": config.transport,
            }
        return status

    def get_tools(self) -> list[BaseTool]:
        """Get all MCP tools as LangChain tools."""
        if self._tools_cache:
            return self._tools_cache

        tools = []
        for _name, conn in self._connections.items():
            if not conn.is_running:
                continue
            for desc in conn.tools:
                tools.append(MCPToolAdapter(hub=self, descriptor=desc))

        self._tools_cache = tools
        return tools

    def get_all_tool_descriptors(self) -> list[MCPToolDescriptor]:
        """Get raw tool descriptors from all running servers."""
        result = []
        for conn in self._connections.values():
            if conn.is_running:
                result.extend(conn.tools)
        return result

    def get_all_resource_descriptors(self) -> list[MCPResourceDescriptor]:
        """Get all resource descriptors from running servers."""
        result = []
        for conn in self._connections.values():
            if conn.is_running:
                result.extend(conn.resources)
        return result

    def call_tool_sync(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Synchronously call a tool on a specific MCP server."""
        conn = self._connections.get(server_name)
        if not conn or not conn.is_running:
            return f"Error: MCP server '{server_name}' is not running."
        try:
            return conn.call_tool(tool_name, arguments)
        except Exception as exc:
            logger.error("MCP tool call failed (%s/%s): %s", server_name, tool_name, exc)
            return f"Error calling MCP tool: {exc}"

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Async wrapper for tool calling (runs sync call in thread)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.call_tool_sync, server_name, tool_name, arguments)

    def read_resource(self, server_name: str, uri: str) -> str:
        """Read a resource from a specific MCP server."""
        conn = self._connections.get(server_name)
        if not conn or not conn.is_running:
            return f"Error: MCP server '{server_name}' is not running."
        try:
            return conn.read_resource(uri)
        except Exception as exc:
            logger.error("MCP resource read failed (%s/%s): %s", server_name, uri, exc)
            return f"Error reading MCP resource: {exc}"

    def __del__(self):
        self.stop_all()
