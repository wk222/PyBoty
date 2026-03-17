"""Tests for private state registry and subagent state isolation."""

from __future__ import annotations

from core.private_state import (
    BUILTIN_PRIVATE_KEYS,
    get_private_keys,
    get_private_keys_by_owner,
    register_private_keys,
)
from core.subagent_runtime import EXCLUDED_SUBAGENT_STATE_KEYS, filter_subagent_state


class TestPrivateStateRegistry:
    def test_builtin_keys_include_messages(self):
        assert "messages" in BUILTIN_PRIVATE_KEYS

    def test_builtin_keys_include_todos(self):
        assert "todos" in BUILTIN_PRIVATE_KEYS

    def test_builtin_keys_include_skills_metadata(self):
        assert "skills_metadata" in BUILTIN_PRIVATE_KEYS

    def test_builtin_keys_include_memory_contents(self):
        assert "memory_contents" in BUILTIN_PRIVATE_KEYS

    def test_get_private_keys_includes_builtins(self):
        keys = get_private_keys()
        for k in BUILTIN_PRIVATE_KEYS:
            assert k in keys

    def test_register_custom_keys(self):
        register_private_keys("TestOwner", {"_test_key_abc"})
        keys = get_private_keys()
        assert "_test_key_abc" in keys

    def test_get_private_keys_by_owner(self):
        owners = get_private_keys_by_owner()
        assert "builtin" in owners
        assert "TodoListMiddleware" in owners
        assert "SummarizationMiddleware" in owners
        assert "MemoryMiddleware" in owners

    def test_middleware_registered_keys(self):
        owners = get_private_keys_by_owner()
        assert "_todo_state" in owners["TodoListMiddleware"]
        assert "_summarization_event" in owners["SummarizationMiddleware"]
        assert "skills_metadata" in owners["SkillsMiddleware"]


class TestSubagentStateFiltering:
    def test_excluded_keys_derived_from_registry(self):
        assert "messages" in EXCLUDED_SUBAGENT_STATE_KEYS
        assert "todos" in EXCLUDED_SUBAGENT_STATE_KEYS

    def test_filter_removes_private_keys(self):
        state = {
            "messages": [{"role": "user", "content": "hi"}],
            "todos": [{"id": "1"}],
            "skills_metadata": [],
            "memory_contents": {"a": "b"},
            "custom_key": "keep_me",
            "workspace_data": {"x": 1},
        }
        filtered = filter_subagent_state(state)
        assert "messages" not in filtered
        assert "todos" not in filtered
        assert "skills_metadata" not in filtered
        assert "memory_contents" not in filtered
        assert filtered["custom_key"] == "keep_me"
        assert filtered["workspace_data"] == {"x": 1}

    def test_filter_empty_state(self):
        assert filter_subagent_state({}) == {}
        assert filter_subagent_state(None) == {}

    def test_filter_uses_dynamic_registry(self):
        register_private_keys("TestFilter", {"_dynamic_test_key"})
        state = {"_dynamic_test_key": "secret", "public": "visible"}
        filtered = filter_subagent_state(state)
        assert "_dynamic_test_key" not in filtered
        assert filtered["public"] == "visible"
