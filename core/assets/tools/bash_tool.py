"""Atomic bash shell execution tool for the agent."""

import os
import re
import subprocess
from typing import Optional, Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from .file_system_tools import _check_path


_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 120
_MAX_OUTPUT_BYTES = 50 * 1024


_BLOCKLIST: list[tuple[re.Pattern, str]] = [
    (re.compile(r"rm\s+-[^\s]*r[^\s]*\s+/(?!\S)"), "rm -rf / (recursive delete of root)"),
    (re.compile(r"rm\s+-[^\s]*r[^\s]*\s+/\s"), "rm -rf / (recursive delete of root)"),
    (re.compile(r":\s*\(\s*\)\s*\{.*:\s*\|.*&.*\}"), "fork bomb"),
    (re.compile(r"mkfs\b"), "mkfs (filesystem format)"),
    (re.compile(r"dd\s+.*of=/dev/(sd|hd|nvme|vd|xvd)"), "dd to raw block device"),
    (re.compile(r">\s*/dev/(sd|hd|nvme|vd|xvd)"), "overwrite block device"),
    (re.compile(r"shutdown\b|halt\b|poweroff\b|reboot\b"), "system power/shutdown command"),
    (re.compile(r"\bchmod\s+-[^\s]*R[^\s]*\s+/(?!\S)"), "chmod -R / (recursive perm change on root)"),
    (re.compile(r"\bchown\s+-[^\s]*R[^\s]*\s+/(?!\S)"), "chown -R / (recursive ownership change on root)"),
]


def _check_blocklist(command: str) -> Optional[str]:
    """Return a rejection message if the command matches a hard-blocked pattern."""
    for pattern, label in _BLOCKLIST:
        if pattern.search(command):
            return f"❌ 命令被拒绝: 包含危险操作 ({label})"
    return None


class BashInput(BaseModel):
    command: str = Field(description="要执行的 shell 命令")
    cwd: str = Field(
        default=".",
        description="命令执行的工作目录（相对于工作区根目录），默认为工作区根目录",
    )
    timeout: int = Field(
        default=_DEFAULT_TIMEOUT,
        ge=1,
        le=_MAX_TIMEOUT,
        description=f"超时时间（秒），默认 {_DEFAULT_TIMEOUT}，最大 {_MAX_TIMEOUT}",
    )


class BashTool(BaseTool):
    name: str = "bash"
    description: str = (
        "在工作区根目录下执行 shell 命令（bash），返回 stdout、stderr 和退出码。"
        "可用于运行测试、调用 CLI 工具（git、pip、pytest、npm 等）、检查系统状态或执行构建步骤。"
        "命令在独立子进程中运行，无持久状态。高危操作需要治理审批。"
    )
    args_schema: Type[BaseModel] = BashInput
    risk_level: str = "high"
    allowed_root: Optional[str] = None

    def _run(self, command: str, cwd: str = ".", timeout: int = _DEFAULT_TIMEOUT) -> str:
        rejection = _check_blocklist(command)
        if rejection:
            return rejection

        ok, result = _check_path(cwd, self.allowed_root)
        if not ok:
            return result
        resolved_cwd = result

        if not os.path.exists(resolved_cwd):
            return f"❌ 执行失败: 工作目录不存在 ({cwd})"
        if not os.path.isdir(resolved_cwd):
            return f"❌ 执行失败: 工作目录路径不是目录 ({cwd})"

        effective_timeout = min(max(1, timeout), _MAX_TIMEOUT)

        try:
            proc = subprocess.run(
                command,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                cwd=resolved_cwd,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            return (
                f"❌ 执行超时: 命令在 {effective_timeout}s 内未完成\n"
                f"命令: {command}"
            )
        except Exception as exc:
            return f"❌ 执行失败: {exc}"

        def _decode(raw: bytes) -> str:
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return repr(raw)

        stdout_raw = proc.stdout
        stderr_raw = proc.stderr

        total = len(stdout_raw) + len(stderr_raw)
        truncated = False
        if total > _MAX_OUTPUT_BYTES:
            truncated = True
            half = _MAX_OUTPUT_BYTES // 2
            if len(stdout_raw) <= half:
                stdout_budget = len(stdout_raw)
                stderr_budget = min(len(stderr_raw), _MAX_OUTPUT_BYTES - stdout_budget)
            elif len(stderr_raw) <= half:
                stderr_budget = len(stderr_raw)
                stdout_budget = min(len(stdout_raw), _MAX_OUTPUT_BYTES - stderr_budget)
            else:
                stdout_budget = half
                stderr_budget = _MAX_OUTPUT_BYTES - half
            stdout_raw = stdout_raw[:stdout_budget]
            stderr_raw = stderr_raw[:stderr_budget]

        stdout = _decode(stdout_raw).strip()
        stderr = _decode(stderr_raw).strip()

        lines: list[str] = [f"退出码: {proc.returncode}"]
        if stdout:
            lines.append(f"--- stdout ---\n{stdout}")
        if stderr:
            lines.append(f"--- stderr ---\n{stderr}")
        if not stdout and not stderr:
            lines.append("（无输出）")
        if truncated:
            lines.append(f"⚠️  输出已截断（超过 {_MAX_OUTPUT_BYTES // 1024} KB 上限）")

        return "\n".join(lines)
