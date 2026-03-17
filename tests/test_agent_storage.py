from __future__ import annotations

from pathlib import Path

from core.agent_storage import AgentDefinition, AgentStorage


def test_agent_storage_persists_updates(tmp_path: Path):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help users.",
        )
    )
    storage.add_tool_to_agent("helper", "search_notes")
    storage.toggle_agent("helper", False)

    reloaded = AgentStorage(str(tmp_path / "agents"))
    saved = reloaded.get_agent("helper")

    assert saved is not None
    assert saved.enabled is False
    assert saved.tools == ["search_notes"]


def test_agent_storage_remove_agent_deletes_directory(tmp_path: Path):
    storage = AgentStorage(str(tmp_path / "agents"))
    storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help users.",
        )
    )

    agent_dir = tmp_path / "agents" / "helper"
    assert agent_dir.exists()

    removed = storage.remove_agent("helper")

    assert removed is True
    assert not agent_dir.exists()
