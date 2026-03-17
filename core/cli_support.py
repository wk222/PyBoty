"""Shared interactive CLI support built on top of the PyBot runtime."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from agent import create_tool_creator_agent
from core.config import get_config
from core.project_paths import ProjectPaths


class CliConfigError(RuntimeError):
    """Raised when the CLI cannot boot from the current config."""


def load_required_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load config.json and require a configured API key for CLI startup."""
    config = get_config(path)
    llm_config = config.get("llm_config", {})
    if not llm_config.get("api_key"):
        raise CliConfigError("找不到 config.json 或缺少 llm_config.api_key，请先运行 python onboard.py。")
    return config


class InteractiveCliApp:
    """Rich-powered terminal UI that reuses the shared PyBot runtime."""

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        config_path: str | Path | None = None,
        paths: ProjectPaths | None = None,
        console: Console | None = None,
        agent_factory: Callable[..., Any] | None = None,
        prompt: Callable[..., str] | None = None,
        confirm: Callable[..., bool] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.console = console or Console()
        self.paths = paths or ProjectPaths.from_root()
        self.paths.ensure_runtime_dirs()
        self.config = config or load_required_config(config_path)
        self._agent_factory = agent_factory or create_tool_creator_agent
        self._prompt = prompt or Prompt.ask
        self._confirm = confirm or Confirm.ask
        self._sleep = sleep or time.sleep
        self.bot: Any | None = None
        self.thread_id = str(self.config.get("agent_config", {}).get("thread_id", "default_session"))

    def _build_bot(self, thread_id: str) -> Any:
        llm_config = self.config.get("llm_config", {})
        return self._agent_factory(
            model=llm_config.get("model", "gpt-4"),
            thread_id=thread_id,
            api_key=llm_config.get("api_key"),
            base_url=llm_config.get("api_base"),
            temperature=float(llm_config.get("temperature", 0.7)),
            paths=self.paths,
        )

    def _require_bot(self) -> Any:
        if self.bot is None:
            raise RuntimeError("智能体未初始化")
        return self.bot

    def initialize_agent(self, *, thread_id: str | None = None) -> None:
        """Create or rebuild the shared PyBot runtime for the active session."""
        if thread_id is not None:
            self.thread_id = thread_id
        self.console.print("[dim]初始化智能体...[/dim]")
        self.bot = self._build_bot(self.thread_id)
        self.console.print("[bold green]✅ 智能体初始化完成！[/bold green]\n")

    def show_tools(self) -> None:
        """Render all persisted tools in the shared workspace."""
        bot = self._require_bot()
        tools = bot.storage.tools
        if not tools:
            self.console.print("\n[yellow]📦 当前没有已创建的工具[/yellow]\n")
            return

        self.console.print(Panel("[bold cyan]已创建的工具列表[/bold cyan]", border_style="cyan"))
        for name, info in tools.items():
            params = info.get("parameters") or []
            params_str = (
                "无" if not params else ", ".join(f"{param['name']}({param.get('type', 'any')})" for param in params)
            )
            self.console.print(f"\n🔧 [bold green]{name}[/bold green]")
            self.console.print(f"   [dim]描述:[/dim] {info.get('description', '')}")
            self.console.print(f"   [dim]参数:[/dim] {params_str}")
            self.console.print(f"   [dim]使用次数:[/dim] {info.get('usage_count', 0)}")
        self.console.print()

    def show_agents(self) -> None:
        """Render all persisted sub-agents."""
        bot = self._require_bot()
        agents = bot.list_agents()
        if not agents:
            self.console.print("\n[yellow]🧠 当前没有已创建的子智能体[/yellow]\n")
            return

        self.console.print(Panel("[bold cyan]已创建的智能体列表[/bold cyan]", border_style="cyan"))
        for name, description in agents.items():
            self.console.print(f"\n🤖 [bold green]{name}[/bold green]")
            self.console.print(f"   [dim]描述:[/dim] {description}")
        self.console.print()

    def show_stats(self) -> None:
        """Render tool usage statistics for the active runtime."""
        bot = self._require_bot()
        stats = bot.get_tool_usage_stats()
        if not stats:
            self.console.print("\n[yellow]📊 暂无工具调用统计[/yellow]\n")
            return

        self.console.print(Panel("[bold cyan]工具调用统计[/bold cyan]", border_style="cyan"))
        for name, count in sorted(stats.items(), key=lambda item: item[1], reverse=True):
            self.console.print(f"  [green]{name}[/green]: {count}")
        self.console.print()

    def show_help(self) -> None:
        """Render supported slash commands."""
        help_text = """
[bold cyan]命令列表[/bold cyan]

  [green]/help[/green]    - 显示此帮助信息
  [green]/tools[/green]   - 显示所有已创建的工具
  [green]/agents[/green]  - 显示所有子智能体
  [green]/stats[/green]   - 显示工具调用统计
  [green]/clear[/green]   - 清除所有工具
  [green]/reset[/green]   - 重置会话
  [green]/quit[/green]    - 退出程序

  [dim]其他输入会直接发送给智能体处理[/dim]
"""
        self.console.print(Panel(help_text, border_style="cyan"))

    def _looks_like_rate_limit(self, response: str) -> bool:
        lowered = response.lower()
        return "429" in lowered or "ratelimit" in lowered or "quota" in lowered

    def chat(self, message: str, *, max_retries: int = 3, retry_delay: int = 15) -> None:
        """Send a message to PyBot and render the response in the terminal."""
        bot = self._require_bot()

        for attempt in range(1, max_retries + 1):
            with self.console.status(
                f"[bold cyan]思考中... (尝试 {attempt}/{max_retries})[/bold cyan]",
                spinner="dots",
            ):
                response = bot.chat(message)

            if self._looks_like_rate_limit(response) and attempt < max_retries:
                self.console.print(
                    f"\n[yellow]⚠️ API 速率限制 (429)，等待 {retry_delay} 秒后进行第 {attempt + 1} 次重试...[/yellow]"
                )
                self._sleep(retry_delay)
                continue

            self.console.print("\n🤖 [bold cyan]助手:[/bold cyan]")
            self.console.print(Markdown(response or "（无回复）"))
            return

    def clear_tools(self) -> None:
        """Delete all persisted tools and rebuild the runtime."""
        bot = self._require_bot()
        if not self._confirm("[bold yellow]⚠️  确认清除所有工具？[/bold yellow]"):
            self.console.print("[dim]❌ 取消操作[/dim]")
            return

        for tool_name in list(bot.list_tools().keys()):
            bot.storage.remove_tool(tool_name)
        self.console.print("[bold green]✅ 已清除所有工具[/bold green]")
        self.initialize_agent(thread_id=self.thread_id)

    def reset_session(self) -> None:
        """Create a fresh chat thread and rebuild the runtime around it."""
        new_thread_id = f"cli_session_{int(time.time())}"
        self.initialize_agent(thread_id=new_thread_id)
        self.console.print(f"[bold green]✅ 会话已重置，新会话ID: {self.thread_id}[/bold green]")

    def handle_command(self, user_input: str) -> bool:
        """Handle slash commands. Returns False when the app should exit."""
        command = user_input.strip().lower()
        if command in {"/quit", "/exit"}:
            self.console.print("\n👋 [bold cyan]再见！[/bold cyan]\n")
            return False
        if command == "/help":
            self.show_help()
            return True
        if command == "/tools":
            self.show_tools()
            return True
        if command == "/agents":
            self.show_agents()
            return True
        if command == "/stats":
            self.show_stats()
            return True
        if command == "/clear":
            self.clear_tools()
            return True
        if command == "/reset":
            self.reset_session()
            return True

        self.console.print(f"[bold red]❌ 未知命令: {command}[/bold red]")
        self.console.print("输入 [green]/help[/green] 查看可用命令")
        return True

    def run(self) -> None:
        """Run the interactive terminal loop."""
        self.console.print(
            Panel.fit(
                "[bold cyan]🤖 PyBot - 交互式命令行[/bold cyan]\n\n"
                "欢迎使用！你可以让我创建各种工具、子智能体，然后直接复用它们。\n"
                "输入 [green]/help[/green] 查看命令列表",
                border_style="cyan",
            )
        )

        try:
            self.initialize_agent()
        except CliConfigError as exc:
            self.console.print(f"[bold red]❌ 初始化失败:[/bold red] {exc}")
            return
        except Exception as exc:  # pragma: no cover - defensive entrypoint guard
            self.console.print(f"[bold red]❌ 初始化失败:[/bold red] {exc}")
            return

        while True:
            try:
                user_input = self._prompt("\n👤 [bold green]你[/bold green]").strip()
                if not user_input:
                    continue
                if user_input.startswith("/"):
                    if not self.handle_command(user_input):
                        return
                    continue
                self.chat(user_input)
            except KeyboardInterrupt:
                self.console.print("\n\n👋 [bold cyan]再见！[/bold cyan]\n")
                return
            except Exception as exc:  # pragma: no cover - defensive terminal loop
                self.console.print(f"\n[bold red]❌ 发生错误:[/bold red] {exc}")
