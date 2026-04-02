"""Diagnostics service — metrics collection, aggregation, and optional OTLP export.

Subscribes to the
global EventBus and collects structured metrics (tokens, cost, duration,
error rates) that can be:

  * Queried in-process via ``DiagnosticsService.get_metrics()``.
  * Exported to OpenTelemetry (traces + metrics) when OTLP is configured.
  * Exposed via a ``/api/diagnostics`` API endpoint.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from core.event_bus import Event, EventBus, EventType

logger = logging.getLogger(__name__)


@dataclass
class MetricBucket:
    """Rolling metric bucket for a single counter/gauge."""

    count: int = 0
    total: float = 0.0
    min_val: float = float("inf")
    max_val: float = float("-inf")
    last_value: float = 0.0
    last_timestamp: float = 0.0

    def record(self, value: float, ts: float | None = None) -> None:
        self.count += 1
        self.total += value
        self.min_val = min(self.min_val, value)
        self.max_val = max(self.max_val, value)
        self.last_value = value
        self.last_timestamp = ts or time.time()

    @property
    def avg(self) -> float:
        return self.total / self.count if self.count else 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "total": round(self.total, 4),
            "avg": round(self.avg, 4),
            "min": self.min_val if self.min_val != float("inf") else 0,
            "max": self.max_val if self.max_val != float("-inf") else 0,
            "last": round(self.last_value, 4),
        }


class DiagnosticsService:
    """Collects and aggregates metrics from EventBus events.

    Automatically subscribes to relevant event types and tracks:
      - model_usage: tokens (prompt + completion), cost
      - agent_duration: time per agent invocation
      - tool_calls: count and error rate per tool
      - workflow_runs: count and duration
      - error_rate: overall error events
    """

    def __init__(self, bus: EventBus | None = None):
        self._lock = threading.Lock()
        self._metrics: dict[str, MetricBucket] = defaultdict(MetricBucket)
        self._per_model: dict[str, MetricBucket] = defaultdict(MetricBucket)
        self._per_tool: dict[str, MetricBucket] = defaultdict(MetricBucket)
        self._tool_errors: dict[str, int] = defaultdict(int)
        self._start_time = time.time()
        self._otel_exporter: Any = None

        if bus is not None:
            self._subscribe(bus)

    def _subscribe(self, bus: EventBus) -> None:
        bus.subscribe(EventType.MODEL_USAGE, self._on_model_usage)
        bus.subscribe(EventType.AGENT_START, self._on_agent_start)
        bus.subscribe(EventType.AGENT_END, self._on_agent_end)
        bus.subscribe(EventType.TOOL_CALL, self._on_tool_call)
        bus.subscribe(EventType.TOOL_RESULT, self._on_tool_result)
        bus.subscribe(EventType.WORKFLOW_START, self._on_workflow_start)
        bus.subscribe(EventType.WORKFLOW_END, self._on_workflow_end)
        bus.subscribe(EventType.ERROR, self._on_error)
        bus.subscribe(EventType.COST_RECORD, self._on_cost_record)
        bus.subscribe(EventType.MODEL_FAILOVER, self._on_model_failover)

    def _on_model_usage(self, event: Event) -> None:
        with self._lock:
            prompt_tokens = event.payload.get("prompt_tokens", 0)
            completion_tokens = event.payload.get("completion_tokens", 0)
            total_tokens = prompt_tokens + completion_tokens
            model = event.payload.get("model", "unknown")

            self._metrics["tokens.prompt"].record(prompt_tokens, event.timestamp)
            self._metrics["tokens.completion"].record(completion_tokens, event.timestamp)
            self._metrics["tokens.total"].record(total_tokens, event.timestamp)
            self._per_model[model].record(total_tokens, event.timestamp)

        self._export_metric("model.tokens", total_tokens, {"model": model})

    def _on_agent_start(self, event: Event) -> None:
        with self._lock:
            self._metrics["agent.starts"].record(1, event.timestamp)

    def _on_agent_end(self, event: Event) -> None:
        with self._lock:
            duration = event.payload.get("duration", 0)
            self._metrics["agent.ends"].record(1, event.timestamp)
            self._metrics["agent.duration"].record(duration, event.timestamp)

    def _on_tool_call(self, event: Event) -> None:
        with self._lock:
            tool_name = event.payload.get("tool", "unknown")
            self._per_tool[tool_name].record(1, event.timestamp)
            self._metrics["tool.calls"].record(1, event.timestamp)

    def _on_tool_result(self, event: Event) -> None:
        with self._lock:
            status = event.payload.get("status", "success")
            if status == "error":
                tool_name = event.payload.get("tool", "unknown")
                self._tool_errors[tool_name] += 1
                self._metrics["tool.errors"].record(1, event.timestamp)

    def _on_workflow_start(self, event: Event) -> None:
        with self._lock:
            self._metrics["workflow.starts"].record(1, event.timestamp)

    def _on_workflow_end(self, event: Event) -> None:
        with self._lock:
            duration = event.payload.get("duration", 0)
            self._metrics["workflow.ends"].record(1, event.timestamp)
            self._metrics["workflow.duration"].record(duration, event.timestamp)

    def _on_error(self, event: Event) -> None:
        with self._lock:
            self._metrics["errors"].record(1, event.timestamp)

    def _on_cost_record(self, event: Event) -> None:
        with self._lock:
            cost = event.payload.get("cost", 0)
            self._metrics["cost.total"].record(cost, event.timestamp)

    def _on_model_failover(self, event: Event) -> None:
        with self._lock:
            self._metrics["model.failovers"].record(1, event.timestamp)

    def get_metrics(self) -> dict[str, Any]:
        """Return a snapshot of all collected metrics."""
        with self._lock:
            result: dict[str, Any] = {
                "uptime_seconds": round(time.time() - self._start_time, 1),
                "summary": {},
                "per_model": {},
                "per_tool": {},
                "tool_errors": dict(self._tool_errors),
            }
            for key, bucket in self._metrics.items():
                result["summary"][key] = bucket.snapshot()
            for model, bucket in self._per_model.items():
                result["per_model"][model] = bucket.snapshot()
            for tool, bucket in self._per_tool.items():
                result["per_tool"][tool] = bucket.snapshot()
            return result

    def configure_otlp(
        self,
        endpoint: str = "http://localhost:4318",
        service_name: str = "pybot",
        insecure: bool = True,
    ) -> bool:
        """Configure OpenTelemetry OTLP HTTP exporter for metrics/traces.

        Returns True if OTLP was configured successfully, False if the
        ``opentelemetry`` package is not installed.
        """
        try:
            from opentelemetry import metrics as otel_metrics
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource

            resource = Resource.create({"service.name": service_name})
            exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", insecure=insecure)
            reader = PeriodicExportingMetricReader(exporter, export_interval_millis=30_000)
            provider = MeterProvider(resource=resource, metric_readers=[reader])
            otel_metrics.set_meter_provider(provider)
            self._otel_exporter = provider.get_meter("pybot")
            logger.info("OTLP metrics exporter configured: %s", endpoint)
            return True
        except ImportError:
            logger.info("OpenTelemetry not installed — OTLP export disabled")
            return False

    def _export_metric(self, name: str, value: float, attributes: dict[str, str] | None = None) -> None:
        if self._otel_exporter is None:
            return
        try:
            counter = self._otel_exporter.create_counter(name)
            counter.add(int(value), attributes or {})
        except Exception:
            pass


_diagnostics: DiagnosticsService | None = None


def get_diagnostics(bus: EventBus | None = None) -> DiagnosticsService:
    """Get or create the singleton DiagnosticsService."""
    global _diagnostics
    if _diagnostics is None:
        _diagnostics = DiagnosticsService(bus=bus)
    return _diagnostics
