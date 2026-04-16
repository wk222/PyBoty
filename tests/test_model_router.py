"""Tests for the smart model router."""

from __future__ import annotations

import pytest

from core.systems.runtime.model_router import (
    ModelRouter,
    ModelTier,
    RoutingDecision,
    TierConfig,
    create_model_router,
)


class TestModelRouterClassification:
    def setup_method(self):
        self.router = ModelRouter()

    def test_simple_greeting_routes_light(self):
        d = self.router.classify("你好")
        assert d.tier == ModelTier.LIGHT

    def test_hello_routes_light(self):
        d = self.router.classify("hello")
        assert d.tier == ModelTier.LIGHT

    def test_complex_task_routes_heavy(self):
        d = self.router.classify("请写一个完整的用户认证系统，包括注册、登录、JWT token管理")
        assert d.tier == ModelTier.HEAVY

    def test_refactor_routes_heavy(self):
        d = self.router.classify("帮我重构这段代码，优化性能")
        assert d.tier == ModelTier.HEAVY

    def test_debug_routes_heavy(self):
        d = self.router.classify("debug this error: TypeError at line 42")
        assert d.tier == ModelTier.HEAVY

    def test_medium_default(self):
        d = self.router.classify("帮我解释一下这个函数的作用")
        assert d.tier == ModelTier.MEDIUM

    def test_long_prompt_routes_heavy(self):
        long_prompt = "请分析以下代码：\n" + "x = 1\n" * 500
        d = self.router.classify(long_prompt)
        assert d.tier == ModelTier.HEAVY

    def test_short_factual_routes_light(self):
        d = self.router.classify("什么是Python")
        assert d.tier == ModelTier.LIGHT


class TestCanvasBias:
    def test_focused_biases_light(self):
        router = ModelRouter(canvas="focused")
        d = router.classify("解释下这个概念")
        assert d.tier in (ModelTier.LIGHT, ModelTier.MEDIUM)

    def test_deep_biases_heavy(self):
        router = ModelRouter(canvas="deep")
        d = router.classify("解释下这个概念")
        assert d.tier == ModelTier.HEAVY


class TestExplicitHint:
    def test_hint_overrides_classification(self):
        router = ModelRouter()
        d = router.classify("你好", hint=ModelTier.HEAVY)
        assert d.tier == ModelTier.HEAVY
        assert d.reason == "explicit_hint"

    def test_string_hint(self):
        router = ModelRouter()
        d = router.classify("你好", hint="light")
        assert d.tier == ModelTier.LIGHT


class TestToolBinding:
    def test_tools_upgrade_light_to_medium(self):
        router = ModelRouter()
        d = router.classify("hello", has_tools=True)
        assert d.tier == ModelTier.MEDIUM


class TestRouterDisabled:
    def test_disabled_always_medium(self):
        router = ModelRouter(enabled=False)
        d = router.classify("写一个完整的系统")
        assert d.tier == ModelTier.MEDIUM
        assert d.reason == "router_disabled"


class TestRouterStats:
    def test_stats_accumulate(self):
        router = ModelRouter()
        router.classify("你好")
        router.classify("写一个完整的系统")
        router.classify("解释一下")
        stats = router.stats.to_dict()
        assert stats["total"] == 3
        assert stats["light"] + stats["medium"] + stats["heavy"] == 3


class TestTierUpdate:
    def test_update_tier_model(self):
        router = ModelRouter()
        router.update_tier(ModelTier.LIGHT, "gpt-3.5-turbo")
        d = router.classify("hello")
        assert d.model_spec == "gpt-3.5-turbo"


class TestFactory:
    def test_create_from_config(self):
        config = {
            "enabled": True,
            "light": {"model": "gpt-4o-mini", "max_tokens": 1024},
            "heavy": {"model": "claude-sonnet-4-20250514", "max_tokens": 32768},
        }
        router = create_model_router(config, default_model="gpt-4o")
        assert router._tiers[ModelTier.LIGHT].model_spec == "gpt-4o-mini"
        assert router._tiers[ModelTier.HEAVY].model_spec == "claude-sonnet-4-20250514"
        assert router._tiers[ModelTier.MEDIUM].model_spec == "gpt-4o"

    def test_create_default(self):
        router = create_model_router()
        assert router._enabled is True
        d = router.classify("你好")
        assert d.model_spec == "gpt-4o-mini"


class TestRoutingDecision:
    def test_decision_fields(self):
        router = ModelRouter()
        d = router.classify("implement a REST API")
        assert isinstance(d, RoutingDecision)
        assert isinstance(d.tier, ModelTier)
        assert isinstance(d.model_spec, str)
        assert "max_tokens" in d.overrides
        assert "temperature" in d.overrides
