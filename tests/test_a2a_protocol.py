"""Tests for Agent-to-Agent communication protocol."""

from __future__ import annotations

import pytest

from core.systems.runtime.a2a_protocol import (
    A2ARegistry,
    A2ATask,
    AgentCard,
    TaskState,
)


@pytest.fixture
def local_card():
    return AgentCard(
        agent_id="pybot-1",
        name="PyBot Instance 1",
        description="Primary instance",
        endpoint="http://localhost:8000",
        capabilities=["code_gen", "rag", "workflow"],
        skills=["python", "data_analysis"],
    )


@pytest.fixture
def peer_card():
    return AgentCard(
        agent_id="pybot-2",
        name="PyBot Instance 2",
        description="Secondary instance",
        endpoint="http://remote:8000",
        capabilities=["image_gen", "translation"],
        skills=["design", "multilingual"],
    )


class TestAgentCard:
    def test_to_dict_roundtrip(self, local_card):
        data = local_card.to_dict()
        restored = AgentCard.from_dict(data)
        assert restored.agent_id == local_card.agent_id
        assert restored.capabilities == local_card.capabilities

    def test_default_protocols(self, local_card):
        assert "a2a/1.0" in local_card.protocols


class TestA2ARegistry:
    def test_register_and_list_peers(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        peers = registry.list_peers()
        assert len(peers) == 1
        assert peers[0]["agent_id"] == "pybot-2"

    def test_unregister_peer(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        assert registry.unregister_peer("pybot-2") is True
        assert registry.unregister_peer("nonexistent") is False
        assert len(registry.list_peers()) == 0

    def test_find_capable_peers(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)

        result = registry.find_capable_peers("translation")
        assert len(result) == 1
        assert result[0].agent_id == "pybot-2"

        result = registry.find_capable_peers("nonexistent")
        assert len(result) == 0

    def test_find_by_skill(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        result = registry.find_capable_peers("design")
        assert len(result) == 1


class TestA2ATask:
    def test_create_task(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        task = registry.create_task(
            receiver_id="pybot-2",
            action="translate",
            payload={"text": "Hello", "target_lang": "zh"},
        )
        assert task.sender_id == "pybot-1"
        assert task.receiver_id == "pybot-2"
        assert task.state == TaskState.PENDING

    def test_update_task(self, local_card):
        registry = A2ARegistry(local_card=local_card)
        task = registry.create_task("peer", "test")
        updated = registry.update_task(
            task.task_id,
            state=TaskState.COMPLETED,
            result={"output": "done"},
        )
        assert updated.state == TaskState.COMPLETED
        assert updated.result == {"output": "done"}

    def test_update_nonexistent_task(self, local_card):
        registry = A2ARegistry(local_card=local_card)
        assert registry.update_task("fake_id", state=TaskState.FAILED) is None

    def test_list_tasks_by_state(self, local_card):
        registry = A2ARegistry(local_card=local_card)
        t1 = registry.create_task("peer", "task1")
        t2 = registry.create_task("peer", "task2")
        registry.update_task(t1.task_id, state=TaskState.COMPLETED)

        pending = registry.list_tasks(state=TaskState.PENDING)
        assert len(pending) == 1
        completed = registry.list_tasks(state=TaskState.COMPLETED)
        assert len(completed) == 1

    def test_receive_task(self, local_card):
        registry = A2ARegistry(local_card=local_card)
        incoming = {
            "task_id": "remote-001",
            "sender_id": "pybot-3",
            "receiver_id": "pybot-1",
            "action": "summarize",
            "payload": {"text": "Long document..."},
        }
        task = registry.receive_task(incoming)
        assert task.task_id == "remote-001"
        assert task.state == TaskState.PENDING

    def test_task_serialization(self):
        task = A2ATask(
            sender_id="a",
            receiver_id="b",
            action="test",
            state=TaskState.IN_PROGRESS,
        )
        data = task.to_dict()
        restored = A2ATask.from_dict(data)
        assert restored.state == TaskState.IN_PROGRESS


class TestRegistryOverview:
    def test_to_dict(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        registry.create_task("pybot-2", "test")
        overview = registry.to_dict()
        assert overview["local_card"]["agent_id"] == "pybot-1"
        assert len(overview["peers"]) == 1
        assert overview["pending_tasks"] == 1
        assert overview["total_tasks"] == 1
