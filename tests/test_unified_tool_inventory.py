"""Tests for UnifiedToolInfo and UnifiedAssetInventory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.assets.tools.unified_tool_info import (
    LAYER_SKILL_TOOL,
    LAYER_TOOL,
    UnifiedToolInfo,
)
from core.assets.tools.unified_tool_inventory import UnifiedAssetInventory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_storage(tools: dict[str, dict]) -> MagicMock:
    storage = MagicMock()
    storage.tools = {
        name: {"name": name, **defn} for name, defn in tools.items()
    }
    storage.list_tools.return_value = {name: defn.get("description", "") for name, defn in tools.items()}
    storage.get_tool.side_effect = lambda n: storage.tools.get(n)
    return storage


def _make_skill_definition(name: str, tools: list[dict], enabled: bool = True) -> MagicMock:
    skill = MagicMock()
    skill.name = name
    skill.description = f"Skill {name}"
    skill.enabled = enabled
    skill.tools = [{"name": t["name"], "description": t.get("description", ""), **t} for t in tools]
    skill.capabilities = [f"tag:{name}"]
    skill.system_prompt_extension = f"# {name} context"
    return skill


def _make_skill_registry(skills: dict[str, MagicMock]) -> MagicMock:
    registry = MagicMock()
    registry.skills = skills
    registry.get_active_tools.return_value = []
    registry.get_skill.side_effect = lambda n: skills.get(n)
    return registry


# ---------------------------------------------------------------------------
# UnifiedToolInfo
# ---------------------------------------------------------------------------

class TestUnifiedToolInfoFromToolDef:
    def test_basic_fields(self):
        info = UnifiedToolInfo.from_tool_def("my_tool", {
            "description": "does stuff",
            "parameters": [{"name": "x", "type": "str"}],
            "dependencies": ["requests"],
            "usage_guide": "call it",
            "usage_count": 5,
            "tags": ["io", "http"],
        })
        assert info.name == "my_tool"
        assert info.description == "does stuff"
        assert info.layer == LAYER_TOOL
        assert info.source == "global"
        assert info.skill_name is None
        assert info.enabled is True
        assert info.usage_count == 5
        assert "io" in info.tags

    def test_missing_optional_fields_default(self):
        info = UnifiedToolInfo.from_tool_def("t", {"description": "x"})
        assert info.parameters == []
        assert info.dependencies == []
        assert info.tags == []
        assert info.usage_guide == ""

    def test_extra_fields_in_metadata(self):
        info = UnifiedToolInfo.from_tool_def("t", {"description": "x", "custom_key": "val"})
        assert info.metadata.get("custom_key") == "val"


class TestUnifiedToolInfoFromSkillToolDef:
    def test_layer_and_source(self):
        info = UnifiedToolInfo.from_skill_tool_def(
            {"name": "db_query", "description": "query db"},
            skill_name="database",
            skill_enabled=True,
            skill_tags=["sql"],
            system_prompt_extension="use SQL",
        )
        assert info.name == "db_query"
        assert info.layer == LAYER_SKILL_TOOL
        assert info.source == "skill:database"
        assert info.skill_name == "database"
        assert info.tags == ["sql"]
        assert info.system_prompt_extension == "use SQL"
        assert info.enabled is True

    def test_disabled_skill(self):
        info = UnifiedToolInfo.from_skill_tool_def(
            {"name": "x"},
            skill_name="mypkg",
            skill_enabled=False,
        )
        assert info.enabled is False

    def test_to_summary(self):
        info = UnifiedToolInfo.from_tool_def("t", {"description": "d"})
        s = info.to_summary()
        assert s["name"] == "t"
        assert "layer" in s
        assert "source" in s


# ---------------------------------------------------------------------------
# UnifiedAssetInventory — no storage
# ---------------------------------------------------------------------------

class TestUnifiedAssetInventoryEmpty:
    def setup_method(self):
        self.inv = UnifiedAssetInventory()

    def test_list_all_empty(self):
        assert self.inv.list_all() == []

    def test_get_missing(self):
        assert self.inv.get("anything") is None

    def test_find_empty(self):
        assert self.inv.find() == []

    def test_enabled_names_empty(self):
        assert self.inv.enabled_names() == []

    def test_build_langchain_tools_empty(self):
        with patch("core.assets.tools.unified_tool_inventory.create_dynamic_tool") as mock_create:
            tools = self.inv.build_langchain_tools()
            assert tools == []
            mock_create.assert_not_called()

    def test_summary_zeros(self):
        s = self.inv.summary()
        assert s["total"] == 0
        assert s["direct_tools"] == 0
        assert s["skill_tools"] == 0


# ---------------------------------------------------------------------------
# UnifiedAssetInventory — tool storage only
# ---------------------------------------------------------------------------

class TestUnifiedAssetInventoryToolsOnly:
    def setup_method(self):
        self.storage = _make_tool_storage({
            "alpha": {"description": "alpha tool", "tags": ["io"]},
            "beta": {"description": "beta tool", "tags": []},
        })
        self.inv = UnifiedAssetInventory(tool_storage=self.storage)

    def test_list_all_includes_all_tools(self):
        items = self.inv.list_all()
        names = {i.name for i in items}
        assert {"alpha", "beta"} == names

    def test_all_are_layer_tool(self):
        for item in self.inv.list_all():
            assert item.layer == LAYER_TOOL
            assert item.source == "global"

    def test_get_existing(self):
        info = self.inv.get("alpha")
        assert info is not None
        assert info.name == "alpha"

    def test_get_missing(self):
        assert self.inv.get("nope") is None

    def test_find_by_query(self):
        results = self.inv.find(query="alpha")
        assert len(results) == 1
        assert results[0].name == "alpha"

    def test_find_by_query_case_insensitive(self):
        results = self.inv.find(query="ALPHA")
        assert len(results) == 1

    def test_find_by_layer_tool(self):
        results = self.inv.find(layer=LAYER_TOOL)
        assert len(results) == 2

    def test_find_by_layer_skill_tool_empty(self):
        results = self.inv.find(layer=LAYER_SKILL_TOOL)
        assert results == []

    def test_find_by_tags(self):
        results = self.inv.find(tags=["io"])
        assert len(results) == 1
        assert results[0].name == "alpha"

    def test_find_by_nonexistent_tag_empty(self):
        assert self.inv.find(tags=["nonexistent"]) == []

    def test_enabled_names(self):
        names = self.inv.enabled_names()
        assert set(names) == {"alpha", "beta"}

    def test_list_by_source(self):
        items = self.inv.list_by_source("global")
        assert len(items) == 2

    def test_summary_counts(self):
        s = self.inv.summary()
        assert s["total"] == 2
        assert s["direct_tools"] == 2
        assert s["skill_tools"] == 0

    def test_build_langchain_tools(self):
        with patch("core.assets.tools.unified_tool_inventory.create_dynamic_tool", side_effect=lambda t: MagicMock(name=t["name"])) as mock_create:
            tools = self.inv.build_langchain_tools()
        assert len(tools) == 2

    def test_build_named_subset(self):
        fake_tool = MagicMock()
        fake_tool.name = "alpha"
        with patch("core.assets.tools.unified_tool_inventory.create_dynamic_tool", return_value=fake_tool):
            tools = self.inv.build_langchain_tools(names=["alpha"])
        assert len(tools) == 1


# ---------------------------------------------------------------------------
# UnifiedAssetInventory — skill registry only
# ---------------------------------------------------------------------------

class TestUnifiedAssetInventorySkillsOnly:
    def setup_method(self):
        skill_a = _make_skill_definition("web_search", [
            {"name": "search_web", "description": "search the web"},
            {"name": "fetch_page", "description": "fetch a URL"},
        ])
        skill_b = _make_skill_definition("database", [
            {"name": "run_query", "description": "run SQL"},
        ], enabled=False)
        self.registry = _make_skill_registry({"web_search": skill_a, "database": skill_b})
        self.inv = UnifiedAssetInventory(skill_registry=self.registry)

    def test_list_all_includes_all_skill_tools(self):
        items = self.inv.list_all()
        names = {i.name for i in items}
        assert names == {"search_web", "fetch_page", "run_query"}

    def test_skill_tool_layer(self):
        for item in self.inv.list_all():
            assert item.layer == LAYER_SKILL_TOOL

    def test_skill_tool_source_prefix(self):
        info = self.inv.get("search_web")
        assert info is not None
        assert info.source == "skill:web_search"
        assert info.skill_name == "web_search"

    def test_disabled_skill_tool_is_disabled(self):
        info = self.inv.get("run_query")
        assert info is not None
        assert info.enabled is False

    def test_enabled_names_excludes_disabled(self):
        names = self.inv.enabled_names()
        assert "run_query" not in names
        assert "search_web" in names

    def test_find_by_layer_skill_tool(self):
        results = self.inv.find(layer=LAYER_SKILL_TOOL)
        assert len(results) == 3

    def test_summary_skill_groups(self):
        s = self.inv.summary()
        assert s["skill_tools"] == 3
        assert set(s["skill_groups"]) == {"web_search", "database"}

    def test_list_by_skill_source(self):
        items = self.inv.list_by_source("skill:web_search")
        assert len(items) == 2


# ---------------------------------------------------------------------------
# UnifiedAssetInventory — merged (both sources)
# ---------------------------------------------------------------------------

class TestUnifiedAssetInventoryMerged:
    def setup_method(self):
        self.storage = _make_tool_storage({
            "alpha": {"description": "direct alpha"},
            "shared_name": {"description": "direct version"},
        })
        skill = _make_skill_definition("mypkg", [
            {"name": "skill_tool_a", "description": "skill a"},
            {"name": "shared_name", "description": "skill version (should be shadowed)"},
        ])
        self.registry = _make_skill_registry({"mypkg": skill})
        self.inv = UnifiedAssetInventory(
            tool_storage=self.storage, skill_registry=self.registry
        )

    def test_direct_tools_win_on_collision(self):
        info = self.inv.get("shared_name")
        assert info is not None
        assert info.layer == LAYER_TOOL
        assert info.description == "direct version"

    def test_total_count_deduped(self):
        items = self.inv.list_all()
        names = [i.name for i in items]
        assert names.count("shared_name") == 1
        assert len(items) == 3

    def test_summary_totals(self):
        s = self.inv.summary()
        assert s["total"] == 3
        assert s["direct_tools"] == 2
        assert s["skill_tools"] == 1
