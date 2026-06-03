"""Structured system health checks — OpenClaw ``doctor``-style diagnostics for PyBot."""

from __future__ import annotations

import importlib
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

CheckFn = Callable[[], "DoctorCheck"]


@dataclass
class DoctorCheck:
    id: str
    name: str
    status: str  # pass | warn | fail | skip
    detail: str
    fix_hint: str = ""


@dataclass
class DoctorReport:
    checks: list[DoctorCheck] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [
                {
                    "id": item.id,
                    "name": item.name,
                    "status": item.status,
                    "detail": item.detail,
                    "fix_hint": item.fix_hint,
                }
                for item in self.checks
            ],
            "summary": self.summary,
            "ready": self.summary.get("fail", 0) == 0,
        }


def _status(*, passed: bool, warn: bool = False) -> str:
    if passed:
        return "pass"
    return "warn" if warn else "fail"


def check_python_version() -> DoctorCheck:
    import sys

    ok = sys.version_info >= (3, 10)
    v = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return DoctorCheck(
        id="python",
        name="Python Version",
        status=_status(passed=ok),
        detail=f"Python {v}",
        fix_hint="Install Python 3.10 or newer.",
    )


def check_api_keys() -> DoctorCheck:
    keys_found: list[str] = []
    for name in ("PYBOT_API_KEYS", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(name):
            keys_found.append(name)
    if keys_found:
        return DoctorCheck(
            id="api_keys",
            name="API Keys",
            status="pass",
            detail=f"Found: {', '.join(keys_found)}",
        )
    return DoctorCheck(
        id="api_keys",
        name="API Keys",
        status="fail",
        detail="No PYBOT_API_KEYS or LLM provider key in environment",
        fix_hint="Run pybot-onboard or set PYBOT_API_KEYS / OPENAI_API_KEY.",
    )


def check_llm_config() -> DoctorCheck:
    try:
        from core.systems.runtime.config_impl import get_config

        cfg = get_config()
        model = cfg.get("llm_config", {}).get("model") or cfg.get("model", "unknown")
        has_key = bool(cfg.get("llm_config", {}).get("api_key"))
        return DoctorCheck(
            id="llm_config",
            name="LLM Configuration",
            status=_status(passed=has_key, warn=not has_key),
            detail=f"model={model}, api_key={'set' if has_key else 'missing'}",
            fix_hint="Configure llm_config.api_key in config.json or via onboard.",
        )
    except Exception as exc:
        return DoctorCheck(
            id="llm_config",
            name="LLM Configuration",
            status="fail",
            detail=str(exc),
            fix_hint="Run pybot-onboard to create config.json.",
        )


def check_workspace(workspace_dir: Path | None = None) -> DoctorCheck:
    ws = workspace_dir or Path("workspace")
    if not ws.exists():
        return DoctorCheck(
            id="workspace",
            name="Workspace",
            status="warn",
            detail="workspace/ directory missing",
            fix_hint="POST /api/doctor/bootstrap to create team workspace templates.",
        )
    templates = ("SOUL.md", "TEAM.md", "RULES.md")
    missing = [name for name in templates if not (ws / name).exists()]
    if missing:
        return DoctorCheck(
            id="workspace",
            name="Workspace",
            status="warn",
            detail=f"Missing templates: {', '.join(missing)}",
            fix_hint="POST /api/doctor/bootstrap to generate team Markdown files.",
        )
    return DoctorCheck(
        id="workspace",
        name="Workspace",
        status="pass",
        detail=f"workspace ready ({len(list(ws.iterdir()))} entries)",
    )


def check_memory(workspace_dir: Path | None = None) -> DoctorCheck:
    ws = workspace_dir or Path("workspace")
    mem_md = ws / "MEMORY.md"
    daily_dir = ws / "memory" / "daily"
    fact_count = 0
    if mem_md.exists():
        fact_count = sum(
            1 for line in mem_md.read_text(encoding="utf-8").splitlines() if line.strip().startswith("-")
        )
    daily_count = len(list(daily_dir.glob("*.md"))) if daily_dir.exists() else 0
    return DoctorCheck(
        id="memory",
        name="Memory System",
        status="pass" if mem_md.exists() or daily_count else "warn",
        detail=f"MEMORY.md facts={fact_count}, daily journals={daily_count}",
        fix_hint="Use chat to accumulate memory or POST /api/memory/distill after conversations.",
    )


def check_channels() -> DoctorCheck:
    modules = [
        ("wechat", "core.systems.integration.channels.wechat_channel"),
        ("wecom", "core.systems.integration.channels.wecom_channel"),
        ("feishu", "core.systems.integration.channels.feishu_channel"),
        ("dingtalk", "core.systems.integration.channels.dingtalk_channel"),
        ("wechat_claw", "core.systems.integration.channels.wechat_claw_channel"),
    ]
    loaded = []
    for name, mod in modules:
        try:
            importlib.import_module(mod)
            loaded.append(name)
        except ImportError:
            pass
    return DoctorCheck(
        id="channels",
        name="Channel Modules",
        status="pass" if loaded else "warn",
        detail=f"{len(loaded)} adapters: {', '.join(loaded) or 'none'}",
        fix_hint="Enable channels in config.json and configure credentials in Integrations.",
    )


def check_plugins() -> DoctorCheck:
    try:
        from core.systems.integration.plugin_manifest import get_plugin_registry

        registry = get_plugin_registry()
        plugins = registry.to_dict()
        loaded = sum(1 for p in plugins if p.get("runtime", {}).get("loaded"))
        return DoctorCheck(
            id="plugins",
            name="Plugins",
            status="pass",
            detail=f"{len(plugins)} registered, {loaded} loaded",
        )
    except Exception as exc:
        return DoctorCheck(
            id="plugins",
            name="Plugins",
            status="warn",
            detail=str(exc),
        )


def check_mcp() -> DoctorCheck:
    config_path = Path("workspace") / "mcp_servers.json"
    if not config_path.exists():
        return DoctorCheck(
            id="mcp",
            name="MCP Servers",
            status="warn",
            detail="No workspace/mcp_servers.json",
            fix_hint="Add MCP server definitions under workspace/mcp_servers.json.",
        )
    return DoctorCheck(
        id="mcp",
        name="MCP Servers",
        status="pass",
        detail=f"Config present: {config_path}",
    )


def check_database(workspace_dir: Path | None = None) -> DoctorCheck:
    ws = workspace_dir or Path("workspace")
    candidates = [ws / "pybot.db", Path("pybot.db")]
    db_path = next((p for p in candidates if p.exists()), None)
    if db_path is None:
        return DoctorCheck(
            id="database",
            name="Database",
            status="warn",
            detail="No SQLite database yet (first run?)",
        )
    try:
        conn = sqlite3.connect(str(db_path))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        names = [t[0] for t in tables]
        return DoctorCheck(
            id="database",
            name="Database",
            status="pass",
            detail=f"{len(names)} tables in {db_path.name}",
        )
    except Exception as exc:
        return DoctorCheck(
            id="database",
            name="Database",
            status="fail",
            detail=str(exc),
        )


def check_openclaw_bridge() -> DoctorCheck:
    try:
        from core.assets.skills.openclaw_compat import try_load_openclaw_config

        path, cfg, err = try_load_openclaw_config()
        if cfg is None:
            return DoctorCheck(
                id="openclaw",
                name="OpenClaw Bridge",
                status="skip",
                detail=err or "No ~/.openclaw/openclaw.json",
                fix_hint="Optional: install OpenClaw skills/channels and import via /api/openclaw/import.",
            )
        channels = cfg.get("channels", {}) if isinstance(cfg.get("channels"), dict) else {}
        return DoctorCheck(
            id="openclaw",
            name="OpenClaw Bridge",
            status="pass",
            detail=f"Config loaded from {path.name}, {len(channels)} channel entries",
        )
    except Exception as exc:
        return DoctorCheck(
            id="openclaw",
            name="OpenClaw Bridge",
            status="warn",
            detail=str(exc),
        )


def run_system_doctor(*, workspace_dir: Path | str | None = None) -> DoctorReport:
    ws = Path(workspace_dir).resolve() if workspace_dir else Path("workspace").resolve()
    fns: list[CheckFn] = [
        check_python_version,
        check_api_keys,
        check_llm_config,
        lambda: check_workspace(ws),
        lambda: check_memory(ws),
        check_channels,
        check_plugins,
        check_mcp,
        lambda: check_database(ws),
        check_openclaw_bridge,
    ]
    checks = [fn() for fn in fns]
    summary = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
    for item in checks:
        summary[item.status] = summary.get(item.status, 0) + 1
    return DoctorReport(checks=checks, summary=summary)


def bootstrap_team_workspace(workspace_dir: Path | str) -> dict[str, Any]:
    """Create team workspace Markdown templates (CowAgent/OpenClaw style)."""
    from core.systems.runtime.workspace_manager import WorkspaceManager

    manager = WorkspaceManager(str(workspace_dir))
    created = manager.ensure_team_templates()
    return {"workspace_dir": str(Path(workspace_dir).resolve()), "created": created}
