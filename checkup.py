"""PyBot system health check — `pybot-checkup`.

Runs diagnostic checks on LLM connectivity, MCP servers,
database, memory system, and key dependencies.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from core.systems.runtime.entrypoints import ensure_utf8_stdio

ensure_utf8_stdio()
console = Console()

PASS = "[bold green]PASS[/bold green]"
FAIL = "[bold red]FAIL[/bold red]"
WARN = "[bold yellow]WARN[/bold yellow]"
SKIP = "[dim]SKIP[/dim]"


def _check_python_version() -> tuple[str, str]:
    v = sys.version_info
    status = PASS if v >= (3, 10) else FAIL
    return status, f"Python {v.major}.{v.minor}.{v.micro}"


def _check_dependency(name: str, import_name: str | None = None) -> tuple[str, str]:
    mod_name = import_name or name
    try:
        mod = importlib.import_module(mod_name)
        ver = getattr(mod, "__version__", getattr(mod, "VERSION", "installed"))
        return PASS, f"{name} {ver}"
    except ImportError:
        return FAIL, f"{name} not installed"


def _check_config() -> tuple[str, str]:
    try:
        from core.systems.runtime.config_impl import get_config
        cfg = get_config()
        model = cfg.get("model", "unknown")
        return PASS, f"config loaded, model={model}"
    except Exception as exc:
        return FAIL, f"config error: {exc}"


def _check_llm_connectivity() -> tuple[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
            if os.environ.get(key):
                return PASS, f"{key} found (length={len(os.environ[key])})"
        return WARN, "No LLM API key found in environment"

    masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
    try:
        from core.systems.runtime.pybot_bootstrap import create_llm_client
        from core.systems.runtime.config_impl import get_config
        cfg = get_config()
        model = cfg.get("model", "gpt-4o-mini")
        t0 = time.time()
        client = create_llm_client(model=model, temperature=0)
        from langchain_core.messages import HumanMessage
        resp = client.invoke([HumanMessage(content="Reply with exactly: OK")])
        elapsed = time.time() - t0
        content = resp.content if hasattr(resp, "content") else str(resp)
        return PASS, f"LLM responded in {elapsed:.1f}s (key={masked})"
    except Exception as exc:
        return FAIL, f"LLM call failed: {exc} (key={masked})"


def _check_database() -> tuple[str, str]:
    db_path = Path("workspace") / "pybot.db"
    if not db_path.exists():
        db_path = Path("pybot.db")
    if not db_path.exists():
        return WARN, "No database file found (first run?)"
    try:
        conn = sqlite3.connect(str(db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        names = [t[0] for t in tables]
        return PASS, f"{len(names)} tables: {', '.join(names[:5])}{'...' if len(names) > 5 else ''}"
    except Exception as exc:
        return FAIL, f"DB error: {exc}"


def _check_memory_dir() -> tuple[str, str]:
    mem_dir = Path("workspace") / "memory"
    if not mem_dir.exists():
        mem_dir = Path("memory")
    if not mem_dir.exists():
        return WARN, "No memory directory (will be created on first distill)"
    daily = list((mem_dir / "daily").glob("*.md")) if (mem_dir / "daily").exists() else []
    archive = list((mem_dir / "archive").glob("*.md")) if (mem_dir / "archive").exists() else []
    memory_md = mem_dir / "MEMORY.md"
    facts = 0
    if memory_md.exists():
        facts = sum(1 for line in memory_md.read_text(encoding="utf-8").splitlines() if line.strip().startswith("-"))
    return PASS, f"daily={len(daily)}, archive={len(archive)}, MEMORY.md facts={facts}"


def _check_mcp() -> tuple[str, str]:
    try:
        from core.systems.integration.mcp_hub import MCPHub
        return PASS, "MCPHub module available"
    except ImportError:
        return WARN, "MCPHub module not found"


def _check_channels() -> tuple[str, str]:
    available = []
    for name, mod in [
        ("WeChat", "core.systems.integration.wechat_channel"),
        ("WeCom", "core.systems.integration.wecom_channel"),
        ("Feishu", "core.systems.integration.feishu_channel"),
        ("DingTalk", "core.systems.integration.dingtalk_channel"),
        ("Terminal", "core.systems.integration.terminal_channel"),
        ("ClawBot", "core.systems.integration.wechat_claw_channel"),
    ]:
        try:
            importlib.import_module(mod)
            available.append(name)
        except ImportError:
            pass
    if available:
        return PASS, f"{len(available)} channels: {', '.join(available)}"
    return WARN, "No channel modules found"


def _check_workspace() -> tuple[str, str]:
    ws = Path("workspace")
    if not ws.exists():
        return WARN, "No workspace directory"
    items = list(ws.iterdir())
    dirs = [i.name for i in items if i.is_dir()]
    files = [i.name for i in items if i.is_file()]
    return PASS, f"{len(dirs)} dirs, {len(files)} files"


def main() -> None:
    console.print("\n[bold]PyBot System Checkup[/bold]\n")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Check", style="dim", width=20)
    table.add_column("Status", width=8)
    table.add_column("Details")

    checks = [
        ("Python Version", _check_python_version),
        ("Configuration", _check_config),
        ("LLM Connectivity", _check_llm_connectivity),
        ("Database", _check_database),
        ("Memory System", _check_memory_dir),
        ("MCP Hub", _check_mcp),
        ("Channels", _check_channels),
        ("Workspace", _check_workspace),
        ("langchain", lambda: _check_dependency("langchain")),
        ("langgraph", lambda: _check_dependency("langgraph")),
        ("fastapi", lambda: _check_dependency("fastapi")),
        ("rich", lambda: _check_dependency("rich")),
    ]

    pass_count = 0
    fail_count = 0
    warn_count = 0

    for name, check_fn in checks:
        try:
            status, detail = check_fn()
        except Exception as exc:
            status, detail = FAIL, str(exc)

        table.add_row(name, status, detail)
        if "PASS" in status:
            pass_count += 1
        elif "FAIL" in status:
            fail_count += 1
        else:
            warn_count += 1

    console.print(table)
    console.print(
        f"\n[bold]Summary:[/bold] "
        f"[green]{pass_count} passed[/green], "
        f"[yellow]{warn_count} warnings[/yellow], "
        f"[red]{fail_count} failed[/red]\n"
    )

    if fail_count > 0:
        console.print("[red]Some checks failed. Please review the details above.[/red]")
        sys.exit(1)
    elif warn_count > 0:
        console.print("[yellow]All critical checks passed, but some warnings need attention.[/yellow]")
    else:
        console.print("[green]All checks passed. PyBot is ready.[/green]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Checkup cancelled.[/yellow]")
        sys.exit(0)
