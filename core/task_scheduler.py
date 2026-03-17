"""
定时任务系统 (TaskScheduler)

支持：
1. 从 SCHEDULE.md 加载定时任务定义
2. 基于 cron 表达式调度任务
3. 任务执行时调用智能体处理
4. 支持任务的启用/禁用
"""

import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


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
        self.tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._agent_callback: Callable | None = None
        self._load_tasks()

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
        print(f"[Scheduler] 执行任务: {task.name}")

        if self._agent_callback:
            attempt = 0
            while attempt <= task.max_retries:
                try:
                    # 如果 prompt 是触发工作流的特殊指令
                    if task.prompt.startswith("TRIGGER_WORKFLOW:"):
                        wf_name = task.prompt.split(":", 1)[1].strip()
                        self._agent_callback(f"请执行工作流 {wf_name}", f"schedule-{task.name}")
                    else:
                        self._agent_callback(task.prompt, f"schedule-{task.name}")
                    task.consecutive_failures = 0
                    task.last_error = None
                    if attempt > 0:
                        print(f"[Scheduler] 任务 {task.name} 第 {attempt + 1} 次尝试成功")
                    return
                except Exception as e:
                    attempt += 1
                    task.consecutive_failures += 1
                    task.last_error = str(e)
                    if attempt <= task.max_retries:
                        delay = task.retry_delay * (2 ** (attempt - 1))
                        print(f"[Scheduler] 任务 {task.name} 第 {attempt} 次执行失败: {e}")
                        print(f"[Scheduler] 将在 {delay:.1f}s 后重试 (第 {attempt + 1}/{task.max_retries + 1} 次)")
                        time.sleep(delay)
                    else:
                        print(f"[Scheduler] 任务 {task.name} 已达最大重试次数 ({task.max_retries})，放弃执行")
                        if task.consecutive_failures >= task.max_retries * 2:
                            print(f"[Scheduler] 任务 {task.name} 连续失败 {task.consecutive_failures} 次，自动禁用")
                            task.enabled = False
                            self._save_tasks()

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
