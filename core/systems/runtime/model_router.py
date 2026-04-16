"""Smart Model Router — route tasks to appropriate models by complexity.

Sits between the agent and model_resolver, classifying each request's
complexity and dispatching it to a cost-appropriate model tier.

Tier System
-----------
  light  → Simple Q&A, greetings, short factual answers  (e.g. gpt-4o-mini)
  medium → Standard tasks, summarization, moderate code   (e.g. gpt-4o)
  heavy  → Complex reasoning, long code gen, planning     (e.g. gpt-4o / claude-sonnet)

The router examines prompt length, detected intent signals, and the
current ExecutionCanvas to decide the tier. Users can override via
configuration or per-request hints.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


@dataclass(frozen=True)
class TierConfig:
    """Model specification for a single tier."""
    model_spec: str
    max_tokens: int
    temperature: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_spec": self.model_spec,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }


_HEAVY_SIGNALS = re.compile(
    r"(?:写[一个]*完整|write\s+(?:a\s+)?(?:full|complete)|重构|refactor|"
    r"设计.*架构|design.*architect|分析.*代码|analyze.*code|"
    r"debug|调试|优化|optimize|implement|实现.*功能|"
    r"plan|计划|规划|step.?by.?step|逐步)",
    re.IGNORECASE,
)

_LIGHT_SIGNALS = re.compile(
    r"(?:你好|hello|hi\b|hey\b|谢谢|thanks?|好的|ok\b|是的|yes\b|"
    r"什么是|what\s+is|定义|define|翻译|translate|"
    r"几点|时间|天气|weather|简单)",
    re.IGNORECASE,
)

_PROMPT_LENGTH_HEAVY = 2000
_PROMPT_LENGTH_LIGHT = 200


@dataclass
class RoutingDecision:
    tier: ModelTier
    reason: str
    model_spec: str
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouterStats:
    total: int = 0
    light: int = 0
    medium: int = 0
    heavy: int = 0

    def record(self, tier: ModelTier) -> None:
        self.total += 1
        if tier == ModelTier.LIGHT:
            self.light += 1
        elif tier == ModelTier.MEDIUM:
            self.medium += 1
        else:
            self.heavy += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "light": self.light,
            "medium": self.medium,
            "heavy": self.heavy,
            "light_pct": round(self.light / max(self.total, 1) * 100, 1),
        }


class ModelRouter:
    """Routes prompts to the best-fit model tier."""

    def __init__(
        self,
        *,
        light: TierConfig | None = None,
        medium: TierConfig | None = None,
        heavy: TierConfig | None = None,
        default_model: str = "gpt-4o",
        canvas: str | None = None,
        enabled: bool = True,
    ):
        self._tiers: dict[ModelTier, TierConfig] = {
            ModelTier.LIGHT: light or TierConfig(
                model_spec="gpt-4o-mini",
                max_tokens=2048,
                temperature=0.3,
            ),
            ModelTier.MEDIUM: medium or TierConfig(
                model_spec=default_model,
                max_tokens=8192,
                temperature=0.7,
            ),
            ModelTier.HEAVY: heavy or TierConfig(
                model_spec=default_model,
                max_tokens=16384,
                temperature=0.7,
            ),
        }
        self._canvas = canvas
        self._enabled = enabled
        self.stats = RouterStats()

    def classify(
        self,
        prompt: str,
        *,
        canvas: str | None = None,
        hint: ModelTier | str | None = None,
        has_tools: bool = False,
    ) -> RoutingDecision:
        """Classify a prompt and return the routing decision.

        Parameters
        ----------
        prompt : str
            The user message or full prompt text.
        canvas : str | None
            Override the router's default canvas for this call.
        hint : ModelTier | str | None
            Explicit tier override (bypasses classification).
        has_tools : bool
            Whether tools are bound to this invocation.
        """
        if not self._enabled:
            tier = ModelTier.MEDIUM
            cfg = self._tiers[tier]
            self.stats.record(tier)
            return RoutingDecision(
                tier=tier,
                reason="router_disabled",
                model_spec=cfg.model_spec,
                overrides={"max_tokens": cfg.max_tokens, "temperature": cfg.temperature},
            )

        if hint:
            tier = ModelTier(hint) if isinstance(hint, str) else hint
            cfg = self._tiers[tier]
            self.stats.record(tier)
            return RoutingDecision(
                tier=tier,
                reason="explicit_hint",
                model_spec=cfg.model_spec,
                overrides={"max_tokens": cfg.max_tokens, "temperature": cfg.temperature},
            )

        active_canvas = canvas or self._canvas or "balanced"

        if active_canvas == "focused":
            tier = self._classify_prompt(prompt, bias_light=True)
        elif active_canvas == "deep":
            tier = self._classify_prompt(prompt, bias_heavy=True)
        else:
            tier = self._classify_prompt(prompt)

        if has_tools and tier == ModelTier.LIGHT:
            tier = ModelTier.MEDIUM

        cfg = self._tiers[tier]
        self.stats.record(tier)

        logger.debug("ModelRouter: %s → %s (%s)", prompt[:60], tier.value, cfg.model_spec)
        return RoutingDecision(
            tier=tier,
            reason=f"auto_{active_canvas}",
            model_spec=cfg.model_spec,
            overrides={"max_tokens": cfg.max_tokens, "temperature": cfg.temperature},
        )

    def _classify_prompt(
        self,
        prompt: str,
        *,
        bias_light: bool = False,
        bias_heavy: bool = False,
    ) -> ModelTier:
        prompt_len = len(prompt)

        if _HEAVY_SIGNALS.search(prompt):
            return ModelTier.HEAVY

        if prompt_len > _PROMPT_LENGTH_HEAVY:
            return ModelTier.HEAVY

        if _LIGHT_SIGNALS.search(prompt) and prompt_len < _PROMPT_LENGTH_LIGHT:
            return ModelTier.LIGHT if not bias_heavy else ModelTier.MEDIUM

        if bias_light and prompt_len < 500:
            return ModelTier.LIGHT

        if bias_heavy:
            return ModelTier.HEAVY

        return ModelTier.MEDIUM

    def get_tier_config(self, tier: ModelTier) -> TierConfig:
        return self._tiers[tier]

    def update_tier(self, tier: ModelTier | str, model_spec: str, **kwargs: Any) -> None:
        """Update model spec for a tier at runtime."""
        tier_enum = ModelTier(tier) if isinstance(tier, str) else tier
        old = self._tiers[tier_enum]
        self._tiers[tier_enum] = TierConfig(
            model_spec=model_spec,
            max_tokens=kwargs.get("max_tokens", old.max_tokens),
            temperature=kwargs.get("temperature", old.temperature),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "canvas": self._canvas,
            "tiers": {t.value: c.to_dict() for t, c in self._tiers.items()},
            "stats": self.stats.to_dict(),
        }


def create_model_router(
    config: dict[str, Any] | None = None,
    *,
    default_model: str = "gpt-4o",
    canvas: str | None = None,
) -> ModelRouter:
    """Factory to create a ModelRouter from a config dict.

    Config format::

        {
            "enabled": true,
            "light": {"model": "gpt-4o-mini", "max_tokens": 2048},
            "medium": {"model": "gpt-4o", "max_tokens": 8192},
            "heavy": {"model": "gpt-4o", "max_tokens": 16384},
        }
    """
    cfg = config or {}
    enabled = cfg.get("enabled", True)
    tiers = {}

    for tier_name in ("light", "medium", "heavy"):
        tier_cfg = cfg.get(tier_name)
        if tier_cfg:
            tiers[tier_name] = TierConfig(
                model_spec=tier_cfg.get("model", default_model),
                max_tokens=tier_cfg.get("max_tokens", 8192),
                temperature=tier_cfg.get("temperature", 0.7),
            )

    return ModelRouter(
        light=tiers.get("light"),
        medium=tiers.get("medium"),
        heavy=tiers.get("heavy"),
        default_model=default_model,
        canvas=canvas,
        enabled=enabled,
    )
