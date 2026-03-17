"""Interactive onboarding flow for first-run PyBot setup."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .config import save_config
from .entrypoints import DEFAULT_WEB_PORT

DEFAULT_API_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"
DEFAULT_THREAD_ID = "default_session"


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


@dataclass(frozen=True)
class OnboardingSettings:
    """Collected first-run settings for building config.json."""

    api_base: str
    api_key: str
    model: str
    temperature: float = 0.2
    thread_id: str = DEFAULT_THREAD_ID

    def to_config(self) -> dict[str, Any]:
        return {
            "llm_config": {
                "api_base": self.api_base,
                "api_key": self.api_key,
                "model": self.model,
                "temperature": self.temperature,
            },
            "agent_config": {
                "thread_id": self.thread_id,
            },
        }


def build_initial_config(
    *,
    api_base: str,
    api_key: str,
    model: str,
    temperature: float = 0.2,
    thread_id: str = DEFAULT_THREAD_ID,
) -> dict[str, Any]:
    """Build the initial config payload persisted by onboarding."""
    return OnboardingSettings(
        api_base=api_base,
        api_key=api_key,
        model=model,
        temperature=temperature,
        thread_id=thread_id,
    ).to_config()


def launch_selected_mode(
    choice: str,
    *,
    executable: str,
    runner: Callable[..., Any] = subprocess.run,
) -> list[str] | None:
    """Launch the selected entrypoint and return the executed command."""
    commands = {
        "1": [executable, "service_mode.py"],
        "2": [executable, "interactive_cli.py"],
        "3": None,
    }
    command = commands[choice]
    if command is not None:
        runner(command, check=False)
    return command


class OnboardingWizard:
    """Interactive first-run wizard with testable prompt and launch hooks."""

    def __init__(
        self,
        *,
        console: Console | None = None,
        prompt: Callable[..., str] | None = None,
        runner: Callable[..., Any] | None = None,
        executable: str | None = None,
        config_path: str | Path | None = None,
        clear_screen_fn: Callable[[], None] | None = None,
    ) -> None:
        self.console = console or Console()
        self._prompt = prompt or Prompt.ask
        self._runner = runner or subprocess.run
        self._executable = executable or sys.executable
        self._config_path = config_path
        self._clear_screen = clear_screen_fn or clear_screen

    def render_welcome(self) -> None:
        self.console.print(
            Panel.fit(
                "[bold cyan]PyBot 初始化向导[/bold cyan]\n"
                "欢迎使用！我将引导你完成系统的初始配置。\n"
                "这只需 1 分钟时间。",
                border_style="cyan",
            )
        )

    def collect_settings(self) -> OnboardingSettings:
        self.console.print("\n[bold yellow]第一步：配置大模型 API[/bold yellow]")
        api_base = self._prompt("请输入 API Base URL", default=DEFAULT_API_BASE)
        api_key = self._prompt("请输入 API Key (输入内容将隐藏)", password=True)
        model = self._prompt("请输入要使用的模型名称", default=DEFAULT_MODEL)
        return OnboardingSettings(api_base=api_base, api_key=api_key, model=model)

    def prompt_launch_mode(self) -> str:
        self.console.print("\n[bold yellow]第二步：选择启动模式[/bold yellow]")
        self.console.print("1. [cyan]Web 服务模式[/cyan] (推荐，提供可视化管理界面)")
        self.console.print("2. [cyan]命令行交互模式[/cyan] (适合极客和快速测试)")
        self.console.print("3. [cyan]暂不启动[/cyan] (退出向导)")
        return self._prompt("请选择", choices=["1", "2", "3"], default="1")

    def launch_choice(self, choice: str) -> list[str] | None:
        if choice == "1":
            self.console.print(
                f"\n🚀 正在启动 Web 服务... 请在浏览器中访问 "
                f"[bold cyan]http://localhost:{DEFAULT_WEB_PORT}[/bold cyan]\n"
            )
        elif choice == "2":
            self.console.print("\n🚀 正在启动命令行模式...\n")
        else:
            self.console.print("\n👋 设置完成，你可以随时手动运行 `python service_mode.py` 启动服务。\n")
            return None
        return launch_selected_mode(choice, executable=self._executable, runner=self._runner)

    def run(self) -> Path:
        """Run the full onboarding flow and return the saved config path."""
        self._clear_screen()
        self.render_welcome()
        settings = self.collect_settings()
        config_path = save_config(settings.to_config(), self._config_path)
        self.console.print(f"\n[bold green]✅ 配置已成功保存到 {config_path}[/bold green]")
        choice = self.prompt_launch_mode()
        self.launch_choice(choice)
        return config_path
