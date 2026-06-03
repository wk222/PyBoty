"""
定时任务系统 (TaskScheduler)

支持：
1. 从 SCHEDULE.md 加载定时任务定义
2. 基于 cron 表达式调度任务
3. 任务执行时调用智能体处理
4. 支持任务的启用/禁用
5. Delivery 配置（webhook/channel/event 投递结果）
6. Stagger 错峰（防止同 cron 任务同时执行）
7. 隔离 session 执行（每次运行独立上下文）
8. 连续失败告警（阈值 + cooldown）
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _cron_field_matches(field: str, value: int, min_val: int = 0, max_val: int = 59) -> bool:
    """Check if *value* matches a single cron field (e.g. ``*/5``, ``1,15``, ``3-7``)."""
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str) if step_str.isdigit() else 1
            part = range_part
        if part == "*":
            if (value - min_val) % step == 0:
                return True
        elif "-" in part:
            lo, hi = part.split("-", 1)
            lo_i, hi_i = int(lo), int(hi)
            if lo_i <= value <= hi_i and (value - lo_i) % step == 0:
                return True
        else:
            if value == int(part):
                return True
    return False


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    """Return ``True`` if *dt* matches a standard 5-field cron expression."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    return (
        _cron_field_matches(minute, dt.minute, 0, 59)
        and _cron_field_matches(hour, dt.hour, 0, 23)
        and _cron_field_matches(dom, dt.day, 1, 31)
        and _cron_field_matches(month, dt.month, 1, 12)
        and _cron_field_matches(dow, dt.weekday(), 0, 6)  # 0=Monday
    )


@dataclass
class CronDelivery:
    """Where and how to deliver the task result after execution."""
    mode: str = "none"  # "none" | "channel" | "webhook" | "event"
    channel: str | None = None
    webhook_url: str | None = None
    best_effort: bool = True
    failure_destination: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"mode": self.mode}
        if self.channel:
            d["channel"] = self.channel
        if self.webhook_url:
            d["webhook_url"] = self.webhook_url
        d["best_effort"] = self.best_effort
        if self.failure_destination:
            d["failure_destination"] = self.failure_destination
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronDelivery:
        return cls(
            mode=data.get("mode", "none"),
            channel=data.get("channel"),
            webhook_url=data.get("webhook_url"),
            best_effort=data.get("best_effort", True),
            failure_destination=data.get("failure_destination"),
        )


def _stagger_delay(task_name: str, cron: str, max_jitter: float = 30.0) -> float:
    """Compute a deterministic stagger delay based on task name hash.

    Prevents multiple tasks with the same cron from firing simultaneously.
    """
    import hashlib
    h = int(hashlib.md5(task_name.encode()).hexdigest()[:8], 16)
    return (h % int(max_jitter * 1000)) / 1000.0


@dataclass
class ScheduledTask:
    name: str
    description: str
    cron: str
    prompt: str
    enabled: bool = False
    last_run: float | None = None
    run_count: int = 0
    max_retries: int = 3
    retry_delay: float = 5.0
    consecutive_failures: int = 0
    last_error: str | None = None
    run_once_at: float | None = None
    delivery: CronDelivery | None = None
    stagger: bool = True
    isolated_session: bool = False
    failure_alert_threshold: int = 5
    failure_alert_cooldown: float = 3600.0
    last_alert_time: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            "description": self.description,
            "cron": self.cron,
            "prompt": self.prompt,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "run_count": self.run_count,
            "max_retries": self.max_retries,
            "retry_delay": self.retry_delay,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "stagger": self.stagger,
            "isolated_session": self.isolated_session,
            "failure_alert_threshold": self.failure_alert_threshold,
        }
        if self.run_once_at is not None:
            d["run_once_at"] = self.run_once_at
        if self.delivery is not None:
            d["delivery"] = self.delivery.to_dict()
        return d


class TaskScheduler:
    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = workspace_dir
        self.schedule_path = os.path.join(workspace_dir, "SCHEDULE.md")
        self._state_path = Path(workspace_dir) / "data" / "scheduler_state.json"
        self.tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._agent_callback: Callable | None = None
        self._approval_queue: Any | None = None
        self._workflow_engine: Any | None = None
        self._execution_history: list[dict[str, Any]] = []
        self._history_limit = 200
        self._load_tasks()
        self._load_run_state()

    def set_approval_queue(self, queue: Any) -> None:
        """Wire the approval queue for timeout scanning."""
        self._approval_queue = queue

    def set_workflow_engine(self, engine: Any) -> None:
        """Wire the PyFlowEngine for durable timer recovery."""
        self._workflow_engine = engine

    def _load_tasks(self):
        if not os.path.exists(self.schedule_path):
            return

        try:
            with open(self.schedule_path, encoding="utf-8") as f:
                content = f.read()

            yaml_match = re.search(r"```yaml\s*\n(.*?)```", content, re.DOTALL)
            if not yaml_match:
                return

            yaml_content = yaml_match.group(1).strip()

            lines = yaml_content.split("\n")
            non_comment_lines = [line for line in lines if not line.strip().startswith("#")]
            yaml_str = "\n".join(non_comment_lines).strip()

            if not yaml_str or yaml_str == "tasks: []":
                return

            try:
                import yaml

                data = yaml.safe_load(yaml_str)
            except ImportError:
                data = self._parse_simple_yaml(yaml_str)

            if data and isinstance(data, dict) and "tasks" in data:
                for task_data in data["tasks"]:
                    if isinstance(task_data, dict) and "name" in task_data:
                        task = ScheduledTask(
                            name=task_data["name"],
                            description=task_data.get("description", ""),
                            cron=task_data.get("cron", ""),
                            prompt=task_data.get("prompt", ""),
                            enabled=task_data.get("enabled", False),
                        )
                        self.tasks[task.name] = task
        except Exception as e:
            print(f"[Scheduler] 加载任务失败: {e}")

    def _parse_simple_yaml(self, yaml_str: str) -> dict | None:
        tasks = []
        current_task = {}

        for line in yaml_str.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- name:"):
                if current_task:
                    tasks.append(current_task)
                current_task = {"name": stripped.split(":", 1)[1].strip().strip('"')}
            elif ":" in stripped and current_task:
                key, val = stripped.split(":", 1)
                key, val = key.strip(), val.strip().strip('"')
                if val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                current_task[key] = val

        if current_task:
            tasks.append(current_task)

        return {"tasks": tasks} if tasks else None

    def _load_run_state(self) -> None:
        """Restore runtime state (last_run, run_count, etc.) from disk."""
        if not self._state_path.exists():
            return
        try:
            with self._state_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            task_states = data.get("tasks", {})
            for name, state in task_states.items():
                task = self.tasks.get(name)
                if task is None:
                    continue
                task.last_run = state.get("last_run")
                task.run_count = state.get("run_count", 0)
                task.consecutive_failures = state.get("consecutive_failures", 0)
                task.last_error = state.get("last_error")
            self._execution_history = data.get("history", [])[-self._history_limit :]
            logger.info("[Scheduler] Restored run state for %d tasks", len(task_states))
        except Exception as exc:
            logger.warning("[Scheduler] Failed to load run state: %s", exc)

    def _save_run_state(self) -> None:
        """Persist runtime state to disk."""
        task_states = {}
        for name, task in self.tasks.items():
            task_states[name] = {
                "last_run": task.last_run,
                "run_count": task.run_count,
                "consecutive_failures": task.consecutive_failures,
                "last_error": task.last_error,
            }
        data = {
            "tasks": task_states,
            "history": self._execution_history[-self._history_limit :],
            "updated_at": time.time(),
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._state_path)
        except Exception as exc:
            logger.warning("[Scheduler] Failed to save run state: %s", exc)

    def get_execution_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent execution history."""
        return self._execution_history[-limit:]

    def _should_run(self, task: ScheduledTask) -> bool:
        if not task.enabled:
            return False

        now_ts = time.time()

        if task.run_once_at is not None:
            if now_ts >= task.run_once_at and task.run_count == 0:
                return True
            return False

        if task.last_run is None:
            return True

        now = datetime.now()
        try:
            if not cron_matches(task.cron, now):
                return False
            elapsed = now_ts - task.last_run
            return elapsed > 55
        except Exception:
            return False

    def set_agent_callback(self, callback: Callable):
        self._agent_callback = callback

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print("[Scheduler] 定时任务调度器已启动")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("[Scheduler] 定时任务调度器已停止")

    def _run_loop(self):
        while self._running:
            # 1. 检查 SCHEDULE.md 中的任务
            for task in list(self.tasks.values()):
                if self._should_run(task):
                    self._execute_task(task)

            # 2. 检查工作流目录中的定时任务 (YAML 定义)
            try:
                wf_dir = os.path.join(self.workspace_dir, "workflows")
                if os.path.exists(wf_dir):
                    for f in os.listdir(wf_dir):
                        if not f.endswith((".yml", ".yaml")):
                            continue
                        try:
                            import yaml

                            with open(os.path.join(wf_dir, f), encoding="utf-8") as fh:
                                data = yaml.safe_load(fh) or {}
                                schedule = data.get("schedule")
                                if schedule:
                                    wf_name = data.get("name", f.rsplit(".", 1)[0])
                                    task_name = f"wf_cron_{wf_name}"
                                    if task_name not in self.tasks:
                                        # 动态注册工作流定时任务
                                        self.tasks[task_name] = ScheduledTask(
                                            name=task_name,
                                            description=f"Auto-schedule for workflow {wf_name}",
                                            cron=schedule,
                                            prompt=f"TRIGGER_WORKFLOW:{wf_name}",
                                            enabled=True,
                                        )
                                    else:
                                        # 更新已存在的 cron
                                        self.tasks[task_name].cron = schedule
                        except Exception:
                            pass
            except Exception as e:
                print(f"[Scheduler] 扫描工作流定时任务失败: {e}")

            # 3. 检查审批超时
            self._check_approval_timeouts()

            # 4. 恢复到期的持久定时器
            self._check_timer_resumes()

            time.sleep(30)

    def _check_approval_timeouts(self) -> None:
        if self._approval_queue is None:
            return
        try:
            now = time.time()
            for req in self._approval_queue.list_pending():
                meta = req.get("metadata", {}) if isinstance(req, dict) else getattr(req, "metadata", {})
                timeout_at = meta.get("timeout_at")
                if timeout_at is None:
                    continue
                if now < float(timeout_at):
                    continue
                action = meta.get("timeout_action", "reject")
                approved = action == "approve"
                req_id = req.get("approval_id", "") if isinstance(req, dict) else getattr(req, "approval_id", "")
                try:
                    self._approval_queue.resolve(
                        req_id,
                        approved=approved,
                        note=f"自动超时{('批准' if approved else '拒绝')}",
                        resolved_by="scheduler:timeout",
                    )
                    logger.info("[Scheduler] Approval %s timed out -> %s", req_id, action)
                except Exception:
                    logger.debug("Failed to resolve timed-out approval %s", req_id)
        except Exception:
            logger.debug("Approval timeout check failed", exc_info=True)

    def _execute_task(self, task: ScheduledTask):
        if task.stagger:
            jitter = _stagger_delay(task.name, task.cron)
            if jitter > 0.5:
                logger.debug("[Scheduler] Stagger delay %.2fs for task %s", jitter, task.name)
                time.sleep(jitter)

        task.last_run = time.time()
        task.run_count += 1
        session_id = f"schedule-{task.name}"
        if task.isolated_session:
            import uuid
            session_id = f"schedule-{task.name}-{uuid.uuid4().hex[:8]}"

        logger.info("[Scheduler] Executing task: %s (session=%s)", task.name, session_id)

        history_entry: dict[str, Any] = {
            "task": task.name,
            "started_at": task.last_run,
            "attempt": 0,
            "success": False,
            "session_id": session_id,
        }

        result_text: str | None = None

        if self._agent_callback:
            attempt = 0
            while attempt <= task.max_retries:
                try:
                    if task.prompt.startswith("TRIGGER_WORKFLOW:"):
                        wf_name = task.prompt.split(":", 1)[1].strip()
                        result_text = self._agent_callback(f"请执行工作流 {wf_name}", session_id)
                    else:
                        result_text = self._agent_callback(task.prompt, session_id)
                    task.consecutive_failures = 0
                    task.last_error = None
                    history_entry["success"] = True
                    history_entry["attempt"] = attempt + 1
                    history_entry["completed_at"] = time.time()
                    break
                except Exception as e:
                    attempt += 1
                    task.consecutive_failures += 1
                    task.last_error = str(e)
                    history_entry["attempt"] = attempt
                    history_entry["error"] = str(e)
                    if attempt <= task.max_retries:
                        delay = task.retry_delay * (2 ** (attempt - 1))
                        logger.warning(
                            "[Scheduler] Task %s attempt %d failed: %s. Retrying in %.1fs",
                            task.name, attempt, e, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "[Scheduler] Task %s exceeded max retries (%d)",
                            task.name, task.max_retries,
                        )
                        history_entry["completed_at"] = time.time()

                        self._check_failure_alert(task)

                        if task.consecutive_failures >= task.max_retries * 2:
                            logger.warning(
                                "[Scheduler] Task %s auto-disabled after %d consecutive failures",
                                task.name, task.consecutive_failures,
                            )
                            task.enabled = False
                            self._save_tasks()

        if history_entry.get("success") and task.delivery and task.delivery.mode != "none":
            self._deliver_result(task, result_text, history_entry)

        if not history_entry.get("success") and task.delivery and task.delivery.failure_destination:
            self._deliver_failure(task, history_entry)

        self._execution_history.append(history_entry)
        if len(self._execution_history) > self._history_limit:
            self._execution_history = self._execution_history[-self._history_limit :]
        self._save_run_state()

    def _check_failure_alert(self, task: ScheduledTask) -> None:
        """Emit alert when consecutive failures exceed threshold (with cooldown)."""
        if task.consecutive_failures < task.failure_alert_threshold:
            return
        now = time.time()
        if now - task.last_alert_time < task.failure_alert_cooldown:
            return
        task.last_alert_time = now
        logger.warning(
            "[ALERT] Task '%s' has %d consecutive failures (threshold=%d)",
            task.name, task.consecutive_failures, task.failure_alert_threshold,
        )
        try:
            from core.systems.runtime.event_bus import Event, EventType, event_bus
            event_bus.emit(Event(
                type=EventType.ERROR,
                payload={
                    "source": "scheduler",
                    "task": task.name,
                    "consecutive_failures": task.consecutive_failures,
                    "last_error": task.last_error,
                    "alert": True,
                },
                source="task_scheduler",
            ))
        except Exception:
            pass

    def _deliver_result(self, task: ScheduledTask, result: str | None, entry: dict[str, Any]) -> None:
        """Deliver successful task result to configured destination."""
        delivery = task.delivery
        if delivery is None:
            return
        try:
            if delivery.mode == "webhook" and delivery.webhook_url:
                import urllib.request
                payload = json.dumps({
                    "task": task.name,
                    "result": (result or "")[:2000],
                    "timestamp": time.time(),
                }).encode()
                req = urllib.request.Request(
                    delivery.webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)
                logger.info("[Scheduler] Delivered result to webhook for task %s", task.name)

            elif delivery.mode == "event":
                from core.systems.runtime.event_bus import Event, EventType, event_bus
                event_bus.emit(Event(
                    type=EventType.SCHEDULE_RUN,
                    payload={
                        "task": task.name,
                        "result": (result or "")[:2000],
                        "channel": delivery.channel,
                        "success": True,
                    },
                    source="task_scheduler",
                ))

            elif delivery.mode == "channel" and delivery.channel:
                logger.info("[Scheduler] Channel delivery for task %s to %s (stub)", task.name, delivery.channel)

        except Exception as exc:
            if not (delivery and delivery.best_effort):
                raise
            logger.warning("[Scheduler] Delivery failed for task %s: %s (best effort)", task.name, exc)

    def _deliver_failure(self, task: ScheduledTask, entry: dict[str, Any]) -> None:
        """Send failure notification to configured failure destination."""
        delivery = task.delivery
        if delivery is None or not delivery.failure_destination:
            return
        try:
            from core.systems.runtime.event_bus import Event, EventType, event_bus
            event_bus.emit(Event(
                type=EventType.ERROR,
                payload={
                    "source": "scheduler_delivery",
                    "task": task.name,
                    "error": entry.get("error", "unknown"),
                    "destination": delivery.failure_destination,
                },
                source="task_scheduler",
            ))
        except Exception:
            logger.debug("Failed to deliver failure notification for task %s", task.name)

    def list_tasks(self) -> list[dict]:
        return [task.to_dict() for task in self.tasks.values()]

    def toggle_task(self, name: str, enabled: bool) -> bool:
        if name not in self.tasks:
            return False
        self.tasks[name].enabled = enabled
        self._save_tasks()
        return True

    def add_task(self, task: ScheduledTask) -> bool:
        self.tasks[task.name] = task
        self._save_tasks()
        return True

    def remove_task(self, name: str) -> bool:
        if name not in self.tasks:
            return False
        del self.tasks[name]
        self._save_tasks()
        return True

    def _save_tasks(self):
        tasks_yaml = "tasks:\n"
        if not self.tasks:
            tasks_yaml = "tasks: []"
        else:
            for task in self.tasks.values():
                tasks_yaml += f"  - name: {task.name}\n"
                tasks_yaml += f'    description: "{task.description}"\n'
                tasks_yaml += f'    cron: "{task.cron}"\n'
                tasks_yaml += f'    prompt: "{task.prompt}"\n'
                tasks_yaml += f"    enabled: {str(task.enabled).lower()}\n"

        content = f"""# SCHEDULE — 定时任务配置

> 定义智能体的自动化周期任务。格式为 YAML 代码块。

## 任务列表

```yaml
{tasks_yaml}
```
"""
        with open(self.schedule_path, "w", encoding="utf-8") as f:
            f.write(content)

    def _check_timer_resumes(self) -> None:
        """Scan paused workflows for expired durable timers and resume them."""
        if self._workflow_engine is None:
            return
        try:
            runs_dir = os.path.join(self.workspace_dir, "workflows", ".runs")
            if not os.path.isdir(runs_dir):
                return
            now = time.time()
            for filename in os.listdir(runs_dir):
                if not filename.endswith(".json"):
                    continue
                filepath = os.path.join(runs_dir, filename)
                try:
                    with open(filepath, encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("status") != "paused":
                        continue
                    variables = data.get("variables", {})
                    resume_token = data.get("resume_token")
                    if not resume_token:
                        continue
                    for key, val in variables.items():
                        if key.endswith("._resume_at") and isinstance(val, (int, float)) and val <= now:
                            wf_id = data.get("id", filename[:-5])
                            try:
                                self._workflow_engine.resume_workflow(
                                    wf_id, resume_token, True,
                                    note="durable_timer_expired",
                                )
                                logger.info("[Scheduler] Resumed timer-paused workflow: %s", wf_id)
                            except Exception:
                                logger.debug("Failed to resume timer workflow %s", wf_id)
                            break
                except Exception:
                    pass
        except Exception:
            logger.debug("Timer resume check failed", exc_info=True)
