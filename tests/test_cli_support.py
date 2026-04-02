from __future__ import annotations

import io

import pytest
from rich.console import Console

from core.systems.runtime.cli_support import CliConfigError, InteractiveCliApp, load_required_config


class FakeStorage:
    def __init__(self) -> None:
        self.tools = {
            "demo_tool": {
                "description": "Demo tool",
                "parameters": [{"name": "value", "type": "str"}],
                "usage_count": 2,
            }
        }

    def remove_tool(self, name: str) -> bool:
        self.tools.pop(name, None)
        return True


class FakeBot:
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.storage = FakeStorage()

    def chat(self, message: str) -> str:
        return f"reply:{message}"

    def list_tools(self) -> dict[str, str]:
        return {name: tool["description"] for name, tool in self.storage.tools.items()}

    def list_agents(self) -> dict[str, str]:
        return {"helper": "Shared helper agent"}

    def get_tool_usage_stats(self) -> dict[str, int]:
        return {"demo_tool": 2}


@pytest.fixture
def cli_config() -> dict:
    return {
        "llm_config": {
            "api_key": "test-key",
            "api_base": "https://example.com/v1",
            "model": "gpt-4o-mini",
            "temperature": 0.2,
        },
        "agent_config": {"thread_id": "seed-thread"},
    }


def make_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=100)


def test_load_required_config_raises_without_api_key(tmp_path):
    with pytest.raises(CliConfigError):
        load_required_config(tmp_path / "missing.json")


def test_reset_command_rebuilds_runtime(temp_paths, cli_config):
    created_threads: list[str] = []

    def fake_agent_factory(**kwargs):
        created_threads.append(kwargs["thread_id"])
        return FakeBot(kwargs["thread_id"])

    app = InteractiveCliApp(
        config=cli_config,
        paths=temp_paths,
        console=make_console(),
        agent_factory=fake_agent_factory,
        confirm=lambda *args, **kwargs: True,
        sleep=lambda *_: None,
    )

    app.initialize_agent()
    original_thread = app.thread_id
    should_continue = app.handle_command("/reset")

    assert should_continue is True
    assert app.thread_id != original_thread
    assert created_threads[0] == "seed-thread"
    assert created_threads[1] == app.thread_id


def test_clear_command_removes_tools_and_rebuilds_runtime(temp_paths, cli_config):
    created_bots: list[FakeBot] = []

    def fake_agent_factory(**kwargs):
        bot = FakeBot(kwargs["thread_id"])
        created_bots.append(bot)
        return bot

    app = InteractiveCliApp(
        config=cli_config,
        paths=temp_paths,
        console=make_console(),
        agent_factory=fake_agent_factory,
        confirm=lambda *args, **kwargs: True,
        sleep=lambda *_: None,
    )

    app.initialize_agent()
    app.handle_command("/clear")

    assert len(created_bots) == 2
    assert created_bots[0].storage.tools == {}


def test_quit_command_stops_loop(temp_paths, cli_config):
    app = InteractiveCliApp(
        config=cli_config,
        paths=temp_paths,
        console=make_console(),
        agent_factory=lambda **kwargs: FakeBot(kwargs["thread_id"]),
        confirm=lambda *args, **kwargs: True,
        sleep=lambda *_: None,
    )

    app.initialize_agent()
    assert app.handle_command("/quit") is False
