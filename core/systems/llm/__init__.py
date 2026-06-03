"""LLM provider resolution, failover, and routing.

This system owns everything about *how PyBot talks to chat models*:

- ``model_resolver``: parse a model spec (string / dict / pre-built) into a
  ready-to-use ``BaseChatModel``.
- ``model_failover``: wrap a primary model with a fallback chain that retries
  transient errors against backup providers.
- ``model_router``: classify prompts by complexity and route them to a
  cost-appropriate tier.

All other code should import from ``core.systems.llm`` rather than reaching
into individual modules; that keeps the surface stable as the LLM stack
evolves.
"""

from core.systems.llm.model_failover import (
    ChatModelWithFailover,
    FailoverAttempt,
    FailoverStats,
    create_failover_model,
)
from core.systems.llm.model_resolver import (
    ModelProviderError,
    ResolvedModel,
    list_all_providers,
    list_available_providers,
    resolve_model,
)
from core.systems.llm.model_router import (
    ModelRouter,
    ModelTier,
    RouterStats,
    RoutingDecision,
    TierConfig,
    create_model_router,
)

__all__ = [
    "ChatModelWithFailover",
    "FailoverAttempt",
    "FailoverStats",
    "ModelProviderError",
    "ModelRouter",
    "ModelTier",
    "ResolvedModel",
    "RouterStats",
    "RoutingDecision",
    "TierConfig",
    "create_failover_model",
    "create_model_router",
    "list_all_providers",
    "list_available_providers",
    "resolve_model",
]
