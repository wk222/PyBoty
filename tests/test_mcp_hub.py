"""Tests for core.mcp_hub — MCP protocol hub."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.mcp_hub import (
    MCPHub,
    MCPResourceDescriptor,
    MCPServerConfig,
    MCPServerConnection,
    MCPToolAdapter,
    MCPToolDescriptor,
)


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig(name="test", command="echo")
        assert cfg.transport == "stdio"
        assert cfg.enabled is True
        assert cfg.args == []
        assert cfg.env == {}


class TestMCPToolDescriptor:
    def test_fields(self):
        td = MCPToolDescriptor(name="query", description="Run SQL", server_name="sqlite")
        assert td.name == "query"
        assert td.server_name == "sqlite"


class TestMCPResourceDescriptor:
    def test_fields(self):
        rd = MCPResourceDescriptor(uri="file://test.db", name="database", description="Main DB")
        assert rd.uri == "file://test.db"


class TestMCPHubConfig:
    def test_creates_default_config(self, tmpdir):
        MCPHub(tmpdir)
        config_path = os.path.join(tmpdir, "mcp_servers.json")
        assert os.path.exists(config_path)
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "mcpServers" in data

    def test_loads_existing_config(self, tmpdir):
        config = {
            "mcpServers": {
                "my_server": {
                    "command": "python",
                    "args": ["-m", "mcp_server"],
                    "enabled": True,
                }
            }
        }
        config_path = os.path.join(tmpdir, "mcp_servers.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)

        hub = MCPHub(tmpdir)
        assert "my_server" in hub._configs
        assert hub._configs["my_server"].command == "python"

    def test_disabled_server_not_started(self, tmpdir):
        config = {
            "mcpServers": {
                "disabled_server": {
                    "command": "echo",
                    "args": ["hello"],
                    "enabled": False,
                }
            }
        }
        config_path = os.path.join(tmpdir, "mcp_servers.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)

        hub = MCPHub(tmpdir)
        result = hub.start_all()
        assert result["disabled_server"] is False

    def test_get_tools_empty_when_no_servers(self, tmpdir):
        hub = MCPHub(tmpdir)
        tools = hub.get_tools()
        assert tools == []


class TestMCPHubServerStatus:
    def test_status_no_connections(self, tmpdir):
        config = {
            "mcpServers": {
                "test_server": {
                    "command": "echo",
                    "args": [],
                    "enabled": True,
                }
            }
        }
        config_path = os.path.join(tmpdir, "mcp_servers.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)

        hub = MCPHub(tmpdir)
        status = hub.get_server_status()
        assert "test_server" in status
        assert status["test_server"]["configured"] is True
        assert status["test_server"]["running"] is False

    def test_start_nonexistent_server(self, tmpdir):
        hub = MCPHub(tmpdir)
        result = hub.start_server("nonexistent")
        assert result is False


class TestMCPHubToolSync:
    def test_call_tool_sync_no_server(self, tmpdir):
        hub = MCPHub(tmpdir)
        result = hub.call_tool_sync("missing", "test", {})
        assert "not running" in result

    def test_read_resource_no_server(self, tmpdir):
        hub = MCPHub(tmpdir)
        result = hub.read_resource("missing", "file://test")
        assert "not running" in result


class TestMCPToolAdapter:
    def test_adapter_name(self, tmpdir):
        hub = MCPHub(tmpdir)
        desc = MCPToolDescriptor(name="query", description="Run a query", server_name="sqlite")
        adapter = MCPToolAdapter(hub=hub, descriptor=desc)
        assert adapter.name == "mcp_sqlite_query"
        assert "query" in adapter.description.lower() or "Run a query" in adapter.description

    def test_adapter_run_returns_error_for_missing_server(self, tmpdir):
        hub = MCPHub(tmpdir)
        desc = MCPToolDescriptor(name="query", description="test", server_name="missing")
        adapter = MCPToolAdapter(hub=hub, descriptor=desc)
        result = adapter._run(sql="SELECT 1")
        assert "not running" in result


class TestMCPServerConnection:
    def test_not_running_initially(self):
        cfg = MCPServerConfig(name="test", command="nonexistent_cmd_xyz")
        conn = MCPServerConnection(cfg)
        assert conn.is_running is False

    def test_start_with_bad_command(self):
        cfg = MCPServerConfig(name="test", command="nonexistent_command_abc123")
        conn = MCPServerConnection(cfg)
        result = conn.start()
        assert result is False
        assert conn.is_running is False

    def test_tools_empty_before_start(self):
        cfg = MCPServerConfig(name="test", command="echo")
        conn = MCPServerConnection(cfg)
        assert conn.tools == []
        assert conn.resources == []


class TestMCPHubDescriptors:
    def test_get_all_tool_descriptors_empty(self, tmpdir):
        hub = MCPHub(tmpdir)
        assert hub.get_all_tool_descriptors() == []

    def test_get_all_resource_descriptors_empty(self, tmpdir):
        hub = MCPHub(tmpdir)
        assert hub.get_all_resource_descriptors() == []


class TestMCPHubLifecycle:
    def test_stop_all_no_crash(self, tmpdir):
        hub = MCPHub(tmpdir)
        hub.stop_all()

    def test_stop_nonexistent_server(self, tmpdir):
        hub = MCPHub(tmpdir)
        hub.stop_server("nope")

    def test_restart_nonexistent(self, tmpdir):
        hub = MCPHub(tmpdir)
        result = hub.restart_server("nope")
        assert result is False
