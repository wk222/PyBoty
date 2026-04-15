from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from agent import PyBot
from core.systems.governance.approval_queue import ApprovalQueue
from core.assets.tools import ToolCallRuntime
from core.assets.tools import DelegatedToolApprovalRuntime
from core.assets.tools import DynamicToolInventory
from core.systems.integration import PluginRegistry, discover_plugins, get_plugin_registry, reset_plugin_registry
from core.systems.governance import AgentControlPolicy
from core.systems.governance.tool_control_runtime import ToolControlRuntime


def _write_plugin(tmp_path: Path, *, plugin_id: str = "demo_plugin") -> Path:
    plugin_dir = tmp_path / plugin_id
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        """
from core.plugin_sdk import on_message, on_tool_call, pybot_plugin

events = []


@pybot_plugin(id="demo_plugin", name="Demo Plugin")
class DemoPlugin:
    def on_load(self, manifest):
        events.append(("load", manifest.id))

    def on_enable(self, manifest):
        events.append(("enable", manifest.id))

    def on_disable(self, manifest):
        events.append(("disable", manifest.id))

    def on_unload(self, manifest):
        events.append(("unload", manifest.id))

    @on_tool_call(when="before")
    def before_tool(self, ctx):
        ctx.arguments["from_plugin"] = "yes"
        if ctx.tool_name == "blocked_tool":
            ctx.block("blocked by plugin")

    @on_tool_call(when="after")
    def after_tool(self, ctx):
        if hasattr(ctx.result, "content"):
            ctx.result.content = "decorated:" + str(ctx.result.content)

    @on_message
    def before_message(self, ctx):
        ctx.content = ctx.content.upper()
""".strip(),
        encoding="utf-8",
    )
    (plugin_dir / "pybot.plugin.json").write_text(
        json.dumps(
            {
                "id": "demo_plugin",
                "name": "Demo Plugin",
                "version": "1.0.0",
                "capabilities": ["hooks"],
                "entry_point": plugin_id,
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )
    return plugin_dir


def _build_tool_runtime(*, registry: PluginRegistry) -> ToolCallRuntime:
    return ToolCallRuntime(
        inventory=DynamicToolInventory(),
        control_runtime=ToolControlRuntime(
            control_policy=AgentControlPolicy.from_config({"mode": "open"}),
            approval_scope="root:test",
        ),
        delegated_runtime=DelegatedToolApprovalRuntime(
            approval_queue=ApprovalQueue(),
            approval_scope="root:test",
        ),
        plugin_registry=registry,
    )


def test_plugin_registry_lifecycle_and_hooks(tmp_path: Path):
    _write_plugin(tmp_path)
    registry = PluginRegistry()

    discovered = discover_plugins([str(tmp_path)], registry=registry)
    assert [item.id for item in discovered] == ["demo_plugin"]

    runtime = registry.enable_plugin("demo_plugin")
    assert runtime.enabled is True
    assert runtime.module is not None
    assert runtime.module.events[:2] == [("load", "demo_plugin"), ("enable", "demo_plugin")]
    assert len(runtime.before_tool_call_handlers) == 1
    assert len(runtime.after_tool_call_handlers) == 1
    assert len(runtime.message_handlers) == 1

    registry.disable_plugin("demo_plugin")
    registry.unload_plugin("demo_plugin")
    assert runtime.module.events[-2:] == [("disable", "demo_plugin"), ("unload", "demo_plugin")]


def test_tool_call_runtime_applies_plugin_before_and_after_hooks(tmp_path: Path):
    _write_plugin(tmp_path)
    registry = PluginRegistry()
    discover_plugins([str(tmp_path)], registry=registry)
    registry.enable_plugin("demo_plugin")
    runtime = _build_tool_runtime(registry=registry)

    request = SimpleNamespace(tool_call={"name": "echo_tool", "args": {"value": "hello"}, "id": "call_1"})
    captured: dict[str, str] = {}

    def handler(req):
        captured["from_plugin"] = req.tool_call["args"]["from_plugin"]
        return ToolMessage(content="ok", tool_call_id="call_1", status="success")

    result = runtime.run_tool_call(request, handler)

    assert captured["from_plugin"] == "yes"
    assert isinstance(result, ToolMessage)
    assert result.content == "decorated:ok"


def test_tool_call_runtime_blocks_when_plugin_cancels(tmp_path: Path):
    _write_plugin(tmp_path)
    registry = PluginRegistry()
    discover_plugins([str(tmp_path)], registry=registry)
    registry.enable_plugin("demo_plugin")
    runtime = _build_tool_runtime(registry=registry)

    request = SimpleNamespace(tool_call={"name": "blocked_tool", "args": {"value": "hello"}, "id": "call_2"})
    called = {"count": 0}

    def handler(_req):
        called["count"] += 1
        return ToolMessage(content="ok", tool_call_id="call_2", status="success")

    result = runtime.run_tool_call(request, handler)

    assert called["count"] == 0
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "blocked by plugin" in result.content


def test_pybot_chat_applies_message_plugin_hooks(tmp_path: Path):
    reset_plugin_registry()
    _write_plugin(tmp_path)
    discover_plugins([str(tmp_path)], registry=get_plugin_registry())
    get_plugin_registry().enable_plugin("demo_plugin")

    class _Bus:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        def record_invocation(self, name: str, ok: bool) -> None:
            self.calls.append((name, ok))

    bot = PyBot.__new__(PyBot)
    bot.storage = SimpleNamespace(tools={})
    bot.capability_bus = _Bus()
    bot.thread_id = "thread-1"
    bot._initialize_agent = lambda: None
    bot._do_invoke = lambda message, *, tools_before: {"response": message}

    result = bot.chat("hello plugin")

    assert result == "HELLO PLUGIN"
    assert bot.capability_bus.calls[-1] == ("chat", True)
