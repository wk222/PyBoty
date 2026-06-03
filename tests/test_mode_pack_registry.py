"""Tests for the ModePack protocol, registry, and built-in pack bootstrap."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from core.modes.builtin_packs import ensure_builtin_packs

# ---------------------------------------------------------------------------
# Import the pack machinery
# ---------------------------------------------------------------------------
from core.modes.pack import (
    BaseModePack,
    ModePack,
    ModePackRegistry,
    get_global_registry,
)
from core.modes.profile import ModeProfile, resolve_mode_profile

# ═══════════════════════════════════════════════════════════════════════════
# 1. Registry unit tests
# ═══════════════════════════════════════════════════════════════════════════


class TestModePackRegistry:
    """Test the registry data structure in isolation."""

    def test_register_and_resolve(self) -> None:
        registry = ModePackRegistry()
        pack = _make_stub_pack("test_mode")
        registry.register(pack)
        assert registry.get("test_mode") is pack

    def test_resolve_unknown_raises(self) -> None:
        registry = ModePackRegistry()
        with pytest.raises(KeyError, match="Unknown mode pack"):
            registry.get("nonexistent")

    def test_get_or_none(self) -> None:
        registry = ModePackRegistry()
        assert registry.get_or_none("absent") is None
        pack = _make_stub_pack("present")
        registry.register(pack)
        assert registry.get_or_none("present") is pack

    def test_unregister(self) -> None:
        registry = ModePackRegistry()
        pack = _make_stub_pack("removable")
        registry.register(pack)
        removed = registry.unregister("removable")
        assert removed is pack
        assert "removable" not in registry

    def test_unregister_missing_returns_none(self) -> None:
        registry = ModePackRegistry()
        assert registry.unregister("missing") is None

    def test_list_all(self) -> None:
        registry = ModePackRegistry()
        a, b = _make_stub_pack("a"), _make_stub_pack("b")
        registry.register(a)
        registry.register(b)
        assert set(registry.names()) == {"a", "b"}
        assert len(registry) == 2

    def test_contains(self) -> None:
        registry = ModePackRegistry()
        registry.register(_make_stub_pack("x"))
        assert "x" in registry
        assert "y" not in registry


# ═══════════════════════════════════════════════════════════════════════════
# 2. Built-in pack bootstrap
# ═══════════════════════════════════════════════════════════════════════════


class TestBuiltinPacks:
    """Verify that ensure_builtin_packs() registers the three packs."""

    def test_builtin_packs_registered(self) -> None:
        ensure_builtin_packs()
        registry = get_global_registry()
        for name in ("assistant", "admin", "app_matrix"):
            pack = registry.get(name)
            assert pack.name == name
            assert isinstance(pack.profile, ModeProfile)

    def test_builtin_packs_idempotent(self) -> None:
        ensure_builtin_packs()
        ensure_builtin_packs()  # Should not error or double-register
        registry = get_global_registry()
        assert len([p for p in registry.list_all() if p.name in ("assistant", "admin", "app_matrix")]) == 3


# ═══════════════════════════════════════════════════════════════════════════
# 3. Pack protocol compliance
# ═══════════════════════════════════════════════════════════════════════════


class TestPackProtocol:
    """Verify that concrete packs satisfy the ModePack protocol."""

    def test_assistant_pack_satisfies_protocol(self) -> None:
        from core.modes.assistant_pack import AssistantPack
        pack = AssistantPack()
        assert isinstance(pack, ModePack)
        assert pack.name == "assistant"
        assert pack.get_api_methods() == {}  # no extra methods
        assert pack.get_tools(None) == []

    def test_admin_pack_satisfies_protocol(self) -> None:
        from core.modes.admin_pack import AdminPack
        pack = AdminPack()
        assert isinstance(pack, ModePack)
        assert pack.name == "admin"
        api = pack.get_api_methods()
        assert "start_admin_loop" in api
        assert "submit_admin_goal" in api

    def test_app_matrix_pack_satisfies_protocol(self) -> None:
        from core.modes.app_matrix_pack import AppMatrixPack
        pack = AppMatrixPack()
        assert isinstance(pack, ModePack)
        assert pack.name == "app_matrix"
        api = pack.get_api_methods()
        # Should expose both admin AND app_matrix methods
        assert "start_admin_loop" in api
        assert "submit_app_matrix_goal" in api
        assert "plan_app_matrix_topology" in api


# ═══════════════════════════════════════════════════════════════════════════
# 4. Fourth-mode plug-in scenario
# ═══════════════════════════════════════════════════════════════════════════


class TestFourthModePlugin:
    """Simulate adding a hypothetical fourth mode without touching agent.py."""

    def test_custom_pack_register_and_resolve(self) -> None:
        """A custom pack can be registered and resolved from the global registry."""
        registry = ModePackRegistry()  # fresh registry
        custom = _make_custom_pack()
        registry.register(custom)
        resolved = registry.get("research")
        assert resolved is custom
        assert resolved.name == "research"
        api = resolved.get_api_methods()
        assert "run_hypothesis" in api

    def test_custom_pack_api_dispatch(self) -> None:
        """Custom pack's API methods can be called through dispatch."""
        custom = _make_custom_pack()
        api = custom.get_api_methods()
        # Simulate host dispatch
        result = api["run_hypothesis"](None, hypothesis="test")
        assert result == {"hypothesis": "test", "result": "validated"}

    def test_custom_pack_prompt(self) -> None:
        custom = _make_custom_pack()
        prompt = custom.get_prompt_section(None)
        assert "research" in prompt.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 5. BaseModePack defaults
# ═══════════════════════════════════════════════════════════════════════════


class TestBaseModePack:
    """Verify the convenience base provides sensible defaults."""

    def test_defaults(self) -> None:
        profile = resolve_mode_profile("assistant")
        pack = BaseModePack(_name="test", _profile=profile)
        assert pack.name == "test"
        assert pack.profile is profile
        # defaults
        pack.initialize(None)  # should not raise
        pack.teardown(None)  # should not raise
        assert pack.get_tools(None) == []
        assert pack.get_prompt_section(None) == ""
        assert pack.get_api_methods() == {}


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_stub_pack(name: str) -> BaseModePack:
    return BaseModePack(_name=name, _profile=resolve_mode_profile("assistant"))


class _ResearchPack(BaseModePack):
    """Hypothetical fourth mode for testing plug-in extensibility."""

    def __init__(self) -> None:
        profile = resolve_mode_profile("assistant")  # borrow assistant profile for testing
        super().__init__(_name="research", _profile=profile)

    def get_prompt_section(self, host: Any) -> str:
        return "你当前处于 Research 模式，专注于假设验证和实验设计。"

    def get_api_methods(self) -> dict[str, Callable[..., Any]]:
        return {
            "run_hypothesis": _api_run_hypothesis,
        }


def _api_run_hypothesis(host: Any, *, hypothesis: str) -> dict[str, Any]:
    return {"hypothesis": hypothesis, "result": "validated"}


def _make_custom_pack() -> _ResearchPack:
    return _ResearchPack()
