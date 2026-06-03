"""Observability system — cost tracking, diagnostics, and tracing setup.

This system collects everything PyBot needs to know about its own execution:

- ``cost_tracker``: a LangChain callback that records token usage and tool calls.
- ``diagnostics``: an event-bus subscriber that aggregates rolling metrics
  (latency, error rates, model failovers, ...).
- ``setup``: tracing-backend bootstrap (LangSmith / Langfuse / Console / none).

Anything that wants to *observe* PyBot should depend on this package; runtime
concerns (config, errors, paths) stay in ``core.systems.runtime``.
"""

from core.systems.observability.cost_tracker import (
    CostSummary,
    CostTracker,
    CostTrackerCallback,
    LLMCallRecord,
    ToolCallRecord,
)
from core.systems.observability.diagnostics import (
    DiagnosticsService,
    MetricBucket,
    get_diagnostics,
)
from core.systems.observability.setup import (
    ObservabilityConfig,
    get_observability_config_from_dict,
    setup_tracing,
)

__all__ = [
    "CostSummary",
    "CostTracker",
    "CostTrackerCallback",
    "DiagnosticsService",
    "LLMCallRecord",
    "MetricBucket",
    "ObservabilityConfig",
    "ToolCallRecord",
    "get_diagnostics",
    "get_observability_config_from_dict",
    "setup_tracing",
]
