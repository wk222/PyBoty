"""Plan/hash/revalidate helpers for host-side execution tools.

This module adds a lightweight approval chain for execution-like tools:

1. Build a normalized execution plan before approval
2. Store a stable content hash + plan hash in approval metadata
3. Revalidate the plan immediately before execution

The initial rollout focuses on PyBot's built-in host execution tools such as
``exec_code`` and ``iterative_test``. The helpers are intentionally generic so
other execution-capable tools can opt in later.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

HOST_EXECUTION_TOOLS = frozenset(
    {
        "exec_code",
        "iterative_test",
        "exec_shell",
        "execute_command",
        "run_command",
        "shell",
    }
)


@dataclass(frozen=True)
class HostExecutionPlan:
    tool_name: str
    execution_kind: str
    language: str = ""
    cwd: str = ""
    timeout_seconds: int = 0
    content_hash: str = ""
    plan_hash: str = ""
    preview: str = ""
    risk_hints: tuple[str, ...] = ()
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "execution_kind": self.execution_kind,
            "language": self.language,
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "content_hash": self.content_hash,
            "plan_hash": self.plan_hash,
            "preview": self.preview,
            "risk_hints": list(self.risk_hints),
            "summary": self.summary,
            "revalidate_before_execute": True,
        }


def build_host_execution_plan(tool_name: str, tool_args: dict[str, Any]) -> HostExecutionPlan | None:
    normalized_name = str(tool_name).strip()
    if normalized_name not in HOST_EXECUTION_TOOLS or not isinstance(tool_args, dict):
        return None

    if normalized_name == "exec_code":
        code = str(tool_args.get("code", ""))
        language = str(tool_args.get("language", "python")).strip().lower() or "python"
        cwd = str(tool_args.get("cwd", "")).strip()
        timeout = _normalize_timeout(tool_args.get("timeout"), default=15)
        summary = f"Execute {language} code in host sandbox"
        preview = _build_preview(code)
        risk_hints = _collect_code_hints(code, language=language)
        normalized_payload = {
            "tool_name": normalized_name,
            "execution_kind": "code",
            "language": language,
            "cwd": cwd,
            "timeout_seconds": timeout,
            "code": _normalize_multiline(code),
        }
        hashes = _hash_payload(normalized_payload)
        return HostExecutionPlan(
            tool_name=normalized_name,
            execution_kind="code",
            language=language,
            cwd=cwd,
            timeout_seconds=timeout,
            content_hash=hashes["content_hash"],
            plan_hash=hashes["plan_hash"],
            preview=preview,
            risk_hints=risk_hints,
            summary=summary,
        )

    if normalized_name == "iterative_test":
        command = str(tool_args.get("test_command", "")).strip()
        app_name = str(tool_args.get("app_name", "")).strip()
        timeout = _normalize_timeout(tool_args.get("timeout"), default=15)
        summary = "Run app validation / custom host test command"
        preview = command or f"Validate app {app_name or '<unknown>'}"
        risk_hints = _collect_shell_hints(command) if command else ("validation-loop",)
        normalized_payload = {
            "tool_name": normalized_name,
            "execution_kind": "shell",
            "app_name": app_name,
            "cwd": f"apps/{app_name}" if app_name else "",
            "timeout_seconds": timeout,
            "command": command,
        }
        hashes = _hash_payload(normalized_payload)
        return HostExecutionPlan(
            tool_name=normalized_name,
            execution_kind="shell",
            cwd=f"apps/{app_name}" if app_name else "",
            timeout_seconds=timeout,
            content_hash=hashes["content_hash"],
            plan_hash=hashes["plan_hash"],
            preview=preview,
            risk_hints=risk_hints,
            summary=summary,
        )

    command = str(
        tool_args.get("command")
        or tool_args.get("cmd")
        or tool_args.get("script")
        or ""
    ).strip()
    cwd = str(tool_args.get("cwd", "")).strip()
    timeout = _normalize_timeout(tool_args.get("timeout"), default=30)
    normalized_payload = {
        "tool_name": normalized_name,
        "execution_kind": "shell",
        "cwd": cwd,
        "timeout_seconds": timeout,
        "command": command,
    }
    hashes = _hash_payload(normalized_payload)
    return HostExecutionPlan(
        tool_name=normalized_name,
        execution_kind="shell",
        cwd=cwd,
        timeout_seconds=timeout,
        content_hash=hashes["content_hash"],
        plan_hash=hashes["plan_hash"],
        preview=command,
        risk_hints=_collect_shell_hints(command),
        summary="Execute host shell command",
    )


def revalidate_host_execution_plan(
    approved_plan: HostExecutionPlan,
    *,
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[bool, HostExecutionPlan | None, str]:
    current_plan = build_host_execution_plan(tool_name, tool_args)
    if current_plan is None:
        return False, None, "当前工具调用不是受管的宿主执行操作"
    if approved_plan.tool_name != current_plan.tool_name:
        return False, current_plan, "审批后的工具名称发生变化"
    if approved_plan.plan_hash != current_plan.plan_hash or approved_plan.content_hash != current_plan.content_hash:
        return (
            False,
            current_plan,
            "审批后的宿主执行内容已变化，revalidate 失败，需要重新审批",
        )
    return True, current_plan, ""


def render_host_execution_plan(plan: HostExecutionPlan) -> str:
    lines = [
        f"执行摘要: {plan.summary}",
        f"执行类型: {plan.execution_kind}",
    ]
    if plan.language:
        lines.append(f"语言: {plan.language}")
    if plan.cwd:
        lines.append(f"工作目录: {plan.cwd}")
    if plan.timeout_seconds:
        lines.append(f"超时: {plan.timeout_seconds}s")
    lines.append(f"内容哈希: {plan.content_hash}")
    lines.append(f"计划哈希: {plan.plan_hash}")
    if plan.preview:
        lines.append(f"预览: {plan.preview}")
    if plan.risk_hints:
        lines.append(f"风险提示: {', '.join(plan.risk_hints)}")
    lines.append("执行前会根据 plan/hash 再次 revalidate。")
    return "\n".join(lines)


def _build_preview(content: str, *, max_len: int = 180) -> str:
    preview = " ".join(_normalize_multiline(content).split())
    if len(preview) <= max_len:
        return preview
    return preview[: max_len - 3] + "..."


def _normalize_multiline(content: str) -> str:
    return "\n".join(line.rstrip() for line in str(content).replace("\r\n", "\n").split("\n")).strip()


def _hash_payload(payload: dict[str, Any]) -> dict[str, str]:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_basis = normalized
    content_hash = hashlib.sha256(content_basis.encode("utf-8")).hexdigest()
    plan_hash = hashlib.sha256((payload.get("tool_name", "") + "::" + normalized).encode("utf-8")).hexdigest()
    return {
        "content_hash": content_hash[:20],
        "plan_hash": plan_hash[:20],
    }


def _normalize_timeout(value: Any, *, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _collect_code_hints(code: str, *, language: str) -> tuple[str, ...]:
    text = code.lower()
    hints: list[str] = []
    if "subprocess" in text or "os.system" in text:
        hints.append("spawns-process")
    if "open(" in text or "pathlib" in text:
        hints.append("touches-files")
    if "requests." in text or "httpx." in text or "fetch(" in text:
        hints.append("network-io")
    if "while true" in text or "while(True)" in code or "for (;;)" in code:
        hints.append("possible-infinite-loop")
    if language in {"javascript", "js", "node"} and "require(" in text:
        hints.append("module-load")
    if not hints:
        hints.append("host-execution")
    return tuple(hints)


def _collect_shell_hints(command: str) -> tuple[str, ...]:
    text = command.lower()
    hints: list[str] = []
    if not text:
        return ("host-execution",)
    if any(token in text for token in (" rm ", " del ", " rmdir ", " drop ", " truncate ")):
        hints.append("destructive")
    if any(token in text for token in ("curl ", "wget ", "invoke-webrequest", "http")):
        hints.append("network-io")
    if any(token in text for token in ("python", "node", "bash", "powershell", "cmd ")):
        hints.append("spawns-process")
    if not hints:
        hints.append("host-execution")
    return tuple(hints)


__all__ = [
    "HOST_EXECUTION_TOOLS",
    "HostExecutionPlan",
    "build_host_execution_plan",
    "render_host_execution_plan",
    "revalidate_host_execution_plan",
]
