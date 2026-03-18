"""
定时任务系统 (TaskScheduler)

支持：
1. 从 SCHEDULE.md 加载定时任务定义
2. 基于 cron 表达式调度任务
3. 任务执行时调用智能体处理
4. 支持任务的启用/禁用
"""

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

    def to_dict(self) -> dict[str, Any]:
        return {
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
        }


class TaskScheduler:
    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = workspace_dir
        self.schedule_path = os.path.join(workspace_dir, "SCHEDULE.md")
        self._state_path = Path(workspace_dir) / "data" / "scheduler_state.json"
        self.tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._agent_callback: Callable | None = None
        self._execution_history: list[dict[str, Any]] = []
        self._history_limit = 200
        self._load_tasks()
        self._load_run_state()

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

        if task.last_run is None:
            return True

        try:
            parts = task.cron.split()
            if len(parts) != 5:
                return False

            now = datetime.now()
            minute, hour = parts[0], parts[1]

            if hour == "*":
                interval_match = re.match(r"\*/(\d+)", minute)
                if interval_match:
                    interval_minutes = int(interval_match.group(1))
                    elapsed = (time.time() - task.last_run) / 60
                    return elapsed >= interval_minutes

            hour_match = re.match(r"\*/(\d+)", hour)
            if hour_match:
                interval_hours = int(hour_match.group(1))
                elapsed = (time.time() - task.last_run) / 3600
                return elapsed >= interval_hours

            if minute != "*" and hour != "*":
                target_min = int(minute)
                target_hour = int(hour)
                if now.hour == target_hour and now.minute == target_min:
                    elapsed = time.time() - task.last_run
                    return elapsed > 60
        except Exception:
            pass

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

            time.sleep(30)

    def _execute_task(self, task: ScheduledTask):
        task.last_run = time.time()
        task.run_count += 1
        logger.info("[Scheduler] Executing task: %s", task.name)

        history_entry: dict[str, Any] = {
            "task": task.name,
            "started_at": task.last_run,
            "attempt": 0,
            "success": False,
        }

        if self._agent_callback:
            attempt = 0
            while attempt <= task.max_retries:
                try:
                    if task.prompt.startswith("TRIGGER_WORKFLOW:"):
                        wf_name = task.prompt.split(":", 1)[1].strip()
                        self._agent_callback(f"请执行工作流 {wf_name}", f"schedule-{task.name}")
                    else:
                        self._agent_callback(task.prompt, f"schedule-{task.name}")
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
                        if task.consecutive_failures >= task.max_retries * 2:
                            logger.warning(
                                "[Scheduler] Task %s auto-disabled after %d consecutive failures",
                                task.name, task.consecutive_failures,
                            )
                            task.enabled = False
                            self._save_tasks()

        self._execution_history.append(history_entry)
        if len(self._execution_history) > self._history_limit:
            self._execution_history = self._execution_history[-self._history_limit :]
        self._save_run_state()

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
