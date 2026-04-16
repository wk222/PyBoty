"""Monitor Task — long-running conditional monitoring mode.

Provides a persistent monitoring loop that:
  1. Periodically checks a data source
  2. Evaluates conditions against the data
  3. Triggers actions when conditions are met
  4. Maintains state across checks for trend analysis

Use cases:
  - Stock price monitoring with buy/sell alerts
  - System health checks
  - Data pipeline monitoring
  - Competitive intelligence
"""

from __future__ import annotations

import json
import logging
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ConditionOp(str, Enum):
    GT = "gt"       # greater than
    LT = "lt"       # less than
    GTE = "gte"     # greater than or equal
    LTE = "lte"     # less than or equal
    EQ = "eq"       # equal
    NEQ = "neq"     # not equal
    CONTAINS = "contains"
    CHANGE_PCT = "change_pct"  # percentage change from baseline


@dataclass
class MonitorCondition:
    """A single condition to evaluate against incoming data."""

    field: str
    op: ConditionOp
    value: Any
    label: str = ""

    def evaluate(self, data: dict[str, Any], baseline: dict[str, Any] | None = None) -> bool:
        actual = data.get(self.field)
        if actual is None:
            return False

        try:
            if self.op == ConditionOp.GT:
                return float(actual) > float(self.value)
            elif self.op == ConditionOp.LT:
                return float(actual) < float(self.value)
            elif self.op == ConditionOp.GTE:
                return float(actual) >= float(self.value)
            elif self.op == ConditionOp.LTE:
                return float(actual) <= float(self.value)
            elif self.op == ConditionOp.EQ:
                return str(actual) == str(self.value)
            elif self.op == ConditionOp.NEQ:
                return str(actual) != str(self.value)
            elif self.op == ConditionOp.CONTAINS:
                return str(self.value) in str(actual)
            elif self.op == ConditionOp.CHANGE_PCT:
                if baseline and self.field in baseline:
                    base_val = float(baseline[self.field])
                    if base_val == 0:
                        return False
                    pct = abs((float(actual) - base_val) / base_val * 100)
                    return pct >= float(self.value)
                return False
        except (ValueError, TypeError):
            return False
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "op": self.op.value,
            "value": self.value,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonitorCondition":
        return cls(
            field=data["field"],
            op=ConditionOp(data["op"]),
            value=data["value"],
            label=data.get("label", ""),
        )


@dataclass
class MonitorAlert:
    """Record of a triggered alert."""

    condition_label: str
    field: str
    actual_value: Any
    threshold: Any
    timestamp: float = field(default_factory=time.time)
    data_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_label": self.condition_label,
            "field": self.field,
            "actual_value": self.actual_value,
            "threshold": self.threshold,
            "timestamp": self.timestamp,
        }


class MonitorTaskState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class MonitorConfig:
    """Full configuration for a monitor task."""

    name: str
    description: str = ""
    check_interval_seconds: int = 60
    conditions: list[MonitorCondition] = field(default_factory=list)
    max_alerts: int = 100
    cooldown_seconds: int = 300  # min time between alerts for same condition
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "check_interval_seconds": self.check_interval_seconds,
            "conditions": [c.to_dict() for c in self.conditions],
            "max_alerts": self.max_alerts,
            "cooldown_seconds": self.cooldown_seconds,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonitorConfig":
        conditions = [MonitorCondition.from_dict(c) for c in data.get("conditions", [])]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            check_interval_seconds=data.get("check_interval_seconds", 60),
            conditions=conditions,
            max_alerts=data.get("max_alerts", 100),
            cooldown_seconds=data.get("cooldown_seconds", 300),
            enabled=data.get("enabled", True),
        )


class MonitorTask:
    """A single long-running monitor with state, conditions, and alert history."""

    def __init__(
        self,
        config: MonitorConfig,
        data_fetcher: Callable[[], list[dict[str, Any]]],
        alert_handler: Callable[[MonitorAlert], None] | None = None,
    ):
        self.config = config
        self._fetcher = data_fetcher
        self._alert_handler = alert_handler
        self._state = MonitorTaskState.IDLE
        self._baseline: dict[str, Any] | None = None
        self._last_data: list[dict[str, Any]] = []
        self._alerts: list[MonitorAlert] = []
        self._check_count = 0
        self._last_check_time: float = 0
        self._last_alert_times: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def state(self) -> MonitorTaskState:
        return self._state

    def start(self) -> None:
        if self._state == MonitorTaskState.RUNNING:
            return
        self._stop_event.clear()
        self._state = MonitorTaskState.RUNNING
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"monitor-{self.config.name}",
            daemon=True,
        )
        self._thread.start()
        logger.info("Monitor '%s' started (interval=%ds)", self.config.name, self.config.check_interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()
        self._state = MonitorTaskState.STOPPED
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def pause(self) -> None:
        self._state = MonitorTaskState.PAUSED

    def resume(self) -> None:
        if self._state == MonitorTaskState.PAUSED:
            self._state = MonitorTaskState.RUNNING

    def check_once(self) -> list[MonitorAlert]:
        """Run a single check cycle. Returns any triggered alerts."""
        new_alerts = []
        try:
            data = self._fetcher()
            self._last_data = data
            self._check_count += 1
            self._last_check_time = time.time()

            for record in data:
                for condition in self.config.conditions:
                    if condition.evaluate(record, self._baseline):
                        ckey = f"{condition.label}:{condition.field}"
                        last_alert = self._last_alert_times.get(ckey, 0)
                        if time.time() - last_alert < self.config.cooldown_seconds:
                            continue

                        alert = MonitorAlert(
                            condition_label=condition.label or f"{condition.field} {condition.op.value} {condition.value}",
                            field=condition.field,
                            actual_value=record.get(condition.field),
                            threshold=condition.value,
                            data_snapshot=dict(record),
                        )
                        new_alerts.append(alert)
                        self._alerts.append(alert)
                        self._last_alert_times[ckey] = time.time()

                        if self._alert_handler:
                            try:
                                self._alert_handler(alert)
                            except Exception as exc:
                                logger.warning("Alert handler error: %s", exc)

            if self._baseline is None and data:
                self._baseline = dict(data[0])

            if len(self._alerts) > self.config.max_alerts:
                self._alerts = self._alerts[-self.config.max_alerts:]

        except Exception as exc:
            logger.warning("Monitor '%s' check failed: %s", self.config.name, exc)
            self._state = MonitorTaskState.ERROR

        return new_alerts

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._state == MonitorTaskState.RUNNING:
                self.check_once()
            self._stop_event.wait(timeout=self.config.check_interval_seconds)

    def get_status(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "state": self._state.value,
            "check_count": self._check_count,
            "last_check": self._last_check_time,
            "alert_count": len(self._alerts),
            "last_data_count": len(self._last_data),
            "baseline": self._baseline,
            "interval": self.config.check_interval_seconds,
        }

    def get_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self._alerts[-limit:]]


class MonitorManager:
    """Manages multiple monitor tasks."""

    def __init__(self):
        self._monitors: dict[str, MonitorTask] = {}

    def create(
        self,
        config: MonitorConfig,
        data_fetcher: Callable[[], list[dict[str, Any]]],
        alert_handler: Callable[[MonitorAlert], None] | None = None,
    ) -> MonitorTask:
        if config.name in self._monitors:
            self._monitors[config.name].stop()
        task = MonitorTask(config, data_fetcher, alert_handler)
        self._monitors[config.name] = task
        return task

    def start(self, name: str) -> bool:
        task = self._monitors.get(name)
        if task:
            task.start()
            return True
        return False

    def stop(self, name: str) -> bool:
        task = self._monitors.get(name)
        if task:
            task.stop()
            return True
        return False

    def remove(self, name: str) -> bool:
        task = self._monitors.pop(name, None)
        if task:
            task.stop()
            return True
        return False

    def list_monitors(self) -> list[dict[str, Any]]:
        return [task.get_status() for task in self._monitors.values()]

    def get_monitor(self, name: str) -> MonitorTask | None:
        return self._monitors.get(name)

    def stop_all(self) -> None:
        for task in self._monitors.values():
            task.stop()
