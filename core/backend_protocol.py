"""
BackendProtocol — 统一后端抽象层

借鉴 DeepAgents 的设计：
- 将文件操作、代码执行统一为 Protocol 接口
- 不同实现（本地文件系统、Docker 沙箱、远程沙箱）走同一接口
- 上层代码（中间件、工具、工作流引擎）只依赖协议，不依赖具体实现

架构层次：
  BackendProtocol（基础：文件读写）
    ├── LocalFilesystemBackend   — 本地文件系统（默认）
    ├── SandboxBackend           — 沙箱执行（未来：Docker / Modal）
    └── CompositeBackend         — 按路径路由到不同后端

  SandboxBackendProtocol（扩展：文件读写 + 代码执行）
    └── LocalSandboxBackend      — 本地 uv/subprocess 执行
"""

import glob as _glob
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

FileOperationError = Literal[
    "file_not_found",
    "permission_denied",
    "is_directory",
    "invalid_path",
]
"""Standardized error codes for file operations.

LLM-actionable: the agent can read these codes and decide how to recover.
"""


@dataclass
class WriteResult:
    """Result from a backend write operation."""

    path: str | None = None
    error: FileOperationError | None = None


@dataclass
class EditResult:
    """Result from a backend edit (search-replace) operation."""

    path: str | None = None
    error: FileOperationError | None = None
    old_found: bool = True


@dataclass
class FileInfo:
    """文件/目录信息。"""

    path: str
    name: str
    is_dir: bool
    size: int = 0
    modified: float = 0


@dataclass
class ExecResult:
    """代码/命令执行结果。"""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    success: bool = True
    truncated: bool = False


@runtime_checkable
class BackendProtocol(Protocol):
    """统一后端协议 — 文件操作抽象。

    所有实现都必须提供这些方法。上层代码只依赖此协议。
    """

    def ls(self, path: str = ".") -> list[FileInfo]:
        """列出目录内容。"""
        ...

    def read(self, path: str) -> str:
        """读取文件内容。"""
        ...

    def write(self, path: str, content: str) -> WriteResult:
        """写入文件（覆盖）。Returns structured result."""
        ...

    def edit(self, path: str, old: str, new: str) -> EditResult:
        """替换文件中的指定内容。Returns structured result."""
        ...

    def exists(self, path: str) -> bool:
        """检查路径是否存在。"""
        ...

    def mkdir(self, path: str) -> None:
        """创建目录（含父目录）。"""
        ...

    def remove(self, path: str) -> bool:
        """删除文件或目录。"""
        ...

    def glob(self, pattern: str) -> list[str]:
        """Glob 匹配文件路径。"""
        ...

    def grep(self, pattern: str, path: str = ".", max_results: int = 50) -> list[dict[str, Any]]:
        """在文件中搜索模式。"""
        ...


@runtime_checkable
class SandboxBackendProtocol(BackendProtocol, Protocol):
    """沙箱后端协议 — 文件操作 + 代码执行。"""

    def execute(self, command: str, timeout: int = 30, cwd: str | None = None) -> ExecResult:
        """执行 shell 命令。"""
        ...

    def run_python(self, code: str, timeout: int = 30, dependencies: list[str] | None = None) -> ExecResult:
        """执行 Python 代码。"""
        ...


class LocalFilesystemBackend:
    """本地文件系统后端实现。"""

    def __init__(self, root_dir: str = "."):
        self.root_dir = os.path.abspath(root_dir)
        os.makedirs(self.root_dir, exist_ok=True)

    def _resolve(self, path: str) -> str:
        """将相对路径解析为绝对路径，防止目录穿越。"""
        if os.path.isabs(path):
            resolved = os.path.abspath(path)
        else:
            resolved = os.path.abspath(os.path.join(self.root_dir, path))
        if not resolved.startswith(self.root_dir):
            raise PermissionError(f"路径 '{path}' 超出根目录 '{self.root_dir}'")
        return resolved

    def ls(self, path: str = ".") -> list[FileInfo]:
        resolved = self._resolve(path)
        if not os.path.isdir(resolved):
            raise FileNotFoundError(f"目录不存在: {path}")
        items = []
        for entry in os.scandir(resolved):
            stat = entry.stat()
            items.append(
                FileInfo(
                    path=os.path.relpath(entry.path, self.root_dir),
                    name=entry.name,
                    is_dir=entry.is_dir(),
                    size=stat.st_size if not entry.is_dir() else 0,
                    modified=stat.st_mtime,
                )
            )
        return sorted(items, key=lambda x: (not x.is_dir, x.name))

    def read(self, path: str) -> str:
        resolved = self._resolve(path)
        with open(resolved, encoding="utf-8") as f:
            return f.read()

    def write(self, path: str, content: str) -> WriteResult:
        try:
            resolved = self._resolve(path)
        except PermissionError:
            return WriteResult(error="permission_denied")
        try:
            os.makedirs(os.path.dirname(resolved), exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)
            return WriteResult(path=os.path.relpath(resolved, self.root_dir))
        except PermissionError:
            return WriteResult(error="permission_denied")
        except OSError:
            return WriteResult(error="invalid_path")

    def edit(self, path: str, old: str, new: str) -> EditResult:
        try:
            resolved = self._resolve(path)
        except PermissionError:
            return EditResult(error="permission_denied")
        if not os.path.exists(resolved):
            return EditResult(error="file_not_found", old_found=False)
        if os.path.isdir(resolved):
            return EditResult(error="is_directory", old_found=False)
        try:
            with open(resolved, encoding="utf-8") as f:
                content = f.read()
        except PermissionError:
            return EditResult(error="permission_denied", old_found=False)
        if old not in content:
            return EditResult(old_found=False)
        content = content.replace(old, new, 1)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return EditResult(path=os.path.relpath(resolved, self.root_dir))

    def exists(self, path: str) -> bool:
        try:
            resolved = self._resolve(path)
            return os.path.exists(resolved)
        except PermissionError:
            return False

    def mkdir(self, path: str) -> None:
        resolved = self._resolve(path)
        os.makedirs(resolved, exist_ok=True)

    def remove(self, path: str) -> bool:
        resolved = self._resolve(path)
        if not os.path.exists(resolved):
            return False
        if os.path.isdir(resolved):
            shutil.rmtree(resolved)
        else:
            os.remove(resolved)
        return True

    def glob(self, pattern: str) -> list[str]:
        resolved_pattern = self._resolve(pattern)
        matches = _glob.glob(resolved_pattern, recursive=True)
        return [os.path.relpath(m, self.root_dir) for m in matches]

    def grep(self, pattern: str, path: str = ".", max_results: int = 50) -> list[dict[str, Any]]:
        resolved = self._resolve(path)
        results = []
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []

        def search_file(filepath: str):
            try:
                with open(filepath, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if compiled.search(line):
                            results.append(
                                {
                                    "file": os.path.relpath(filepath, self.root_dir),
                                    "line": i,
                                    "content": line.rstrip()[:200],
                                }
                            )
                            if len(results) >= max_results:
                                return
            except (OSError, UnicodeDecodeError):
                pass

        if os.path.isfile(resolved):
            search_file(resolved)
        else:
            for root, dirs, files in os.walk(resolved):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in files:
                    if len(results) >= max_results:
                        return results
                    search_file(os.path.join(root, fname))
        return results


class LocalSandboxBackend(LocalFilesystemBackend):
    """本地沙箱后端 — 文件操作 + 命令/代码执行。"""

    _STDOUT_LIMIT = 5000
    _STDERR_LIMIT = 2000

    def execute(self, command: str, timeout: int = 30, cwd: str | None = None) -> ExecResult:
        work_dir = self._resolve(cwd) if cwd else self.root_dir
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=min(timeout, 120),
                cwd=work_dir,
            )
            stdout_raw, stderr_raw = proc.stdout, proc.stderr
            was_truncated = len(stdout_raw) > self._STDOUT_LIMIT or len(stderr_raw) > self._STDERR_LIMIT
            return ExecResult(
                stdout=stdout_raw[-self._STDOUT_LIMIT :],
                stderr=stderr_raw[-self._STDERR_LIMIT :],
                returncode=proc.returncode,
                success=proc.returncode == 0,
                truncated=was_truncated,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                stderr=f"命令超时 ({timeout}s): {command}",
                returncode=-1,
                success=False,
            )

    def run_python(self, code: str, timeout: int = 30, dependencies: list[str] | None = None) -> ExecResult:
        import uuid

        tmp_dir = os.path.join(self.root_dir, ".sandbox", uuid.uuid4().hex[:8])
        os.makedirs(tmp_dir, exist_ok=True)
        script = os.path.join(tmp_dir, "run.py")

        deps = dependencies or []
        header = f"""# /// script
# requires-python = ">=3.10"
# dependencies = {json.dumps(deps)}
# ///
"""
        with open(script, "w", encoding="utf-8") as f:
            f.write(header + code)

        has_uv = shutil.which("uv") is not None
        cmd = ["uv", "run", script] if has_uv else [sys.executable, script]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=min(timeout, 120),
                cwd=self.root_dir,
            )
            stdout_raw, stderr_raw = proc.stdout, proc.stderr
            was_truncated = len(stdout_raw) > self._STDOUT_LIMIT or len(stderr_raw) > self._STDERR_LIMIT
            return ExecResult(
                stdout=stdout_raw[-self._STDOUT_LIMIT :],
                stderr=stderr_raw[-self._STDERR_LIMIT :],
                returncode=proc.returncode,
                success=proc.returncode == 0,
                truncated=was_truncated,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                stderr=f"代码执行超时 ({timeout}s)",
                returncode=-1,
                success=False,
            )
        finally:
            try:
                shutil.rmtree(tmp_dir)
            except OSError:
                pass


class CompositeBackend:
    """复合后端 — 按路径前缀路由到不同后端。

    用法示例:
        composite = CompositeBackend(default=LocalFilesystemBackend("workspace"))
        composite.mount("/sandbox", LocalSandboxBackend("/tmp/sandbox"))
    """

    def __init__(self, default: BackendProtocol):
        self._default = default
        self._mounts: list[tuple[str, BackendProtocol]] = []

    def mount(self, prefix: str, backend: BackendProtocol) -> "CompositeBackend":
        prefix = prefix.rstrip("/")
        self._mounts.append((prefix, backend))
        self._mounts.sort(key=lambda x: -len(x[0]))
        return self

    def _route(self, path: str) -> tuple[BackendProtocol, str]:
        normalized = path.replace("\\", "/")
        for prefix, backend in self._mounts:
            if normalized.startswith(prefix + "/") or normalized == prefix:
                relative = normalized[len(prefix) :].lstrip("/") or "."
                return backend, relative
        return self._default, path

    def ls(self, path: str = ".") -> list[FileInfo]:
        backend, resolved = self._route(path)
        return backend.ls(resolved)

    def read(self, path: str) -> str:
        backend, resolved = self._route(path)
        return backend.read(resolved)

    def write(self, path: str, content: str) -> WriteResult:
        backend, resolved = self._route(path)
        return backend.write(resolved, content)

    def edit(self, path: str, old: str, new: str) -> EditResult:
        backend, resolved = self._route(path)
        return backend.edit(resolved, old, new)

    def exists(self, path: str) -> bool:
        backend, resolved = self._route(path)
        return backend.exists(resolved)

    def mkdir(self, path: str) -> None:
        backend, resolved = self._route(path)
        backend.mkdir(resolved)

    def remove(self, path: str) -> bool:
        backend, resolved = self._route(path)
        return backend.remove(resolved)

    def glob(self, pattern: str) -> list[str]:
        backend, resolved = self._route(pattern)
        return backend.glob(resolved)

    def grep(self, pattern: str, path: str = ".", max_results: int = 50) -> list[dict[str, Any]]:
        backend, resolved = self._route(path)
        return backend.grep(pattern, resolved, max_results)


BackendFactory = Callable[..., "BackendProtocol"]
"""A callable that lazily creates a backend instance.

Inspired by DeepAgents' ``BackendFactory`` pattern: backends can be
passed as either an instance or a factory ``Callable[[ToolRuntime], Backend]``.
This allows middleware to defer backend creation until runtime context
(tool runtime, state, config) is available.
"""


def resolve_backend(
    backend: "BackendProtocol | BackendFactory",
    *args: Any,
    **kwargs: Any,
) -> "BackendProtocol":
    """Resolve a backend from an instance or factory.

    If ``backend`` is already a ``BackendProtocol`` instance, returns it.
    If it's callable (a factory), calls it with the provided args/kwargs.
    """
    if isinstance(backend, BackendProtocol):
        return backend
    if callable(backend):
        return backend(*args, **kwargs)
    return backend
