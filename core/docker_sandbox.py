"""Docker-based sandbox backend for subagent execution.

Inspired by DeepAgents' partner sandbox implementations (Runloop, Modal, Daytona).
Implements ``SandboxBackendProtocol`` by running commands inside Docker containers,
providing process-level isolation for untrusted subagent code execution.

Requires ``docker`` Python SDK (``pip install docker``).
Falls back gracefully if Docker is not available.
"""

from __future__ import annotations

import io
import os
import tarfile
from typing import Any

from .backend_protocol import (
    EditResult,
    ExecResult,
    FileInfo,
    WriteResult,
)


class DockerSandboxBackend:
    """Sandbox backend that executes commands inside a Docker container.

    Each instance manages a single long-lived container. The container is
    created lazily on first use and removed on ``close()`` or ``__del__``.
    """

    _STDOUT_LIMIT = 5000
    _STDERR_LIMIT = 2000

    def __init__(
        self,
        root_dir: str = "/workspace",
        *,
        image: str = "python:3.12-slim",
        container_name: str | None = None,
        mem_limit: str = "512m",
        cpu_period: int = 100_000,
        cpu_quota: int = 50_000,
        network_disabled: bool = False,
    ):
        self.root_dir = root_dir
        self.image = image
        self.container_name = container_name
        self.mem_limit = mem_limit
        self.cpu_period = cpu_period
        self.cpu_quota = cpu_quota
        self.network_disabled = network_disabled

        self._client: Any = None
        self._container: Any = None

    def _ensure_container(self) -> Any:
        if self._container is not None:
            try:
                self._container.reload()
                if self._container.status == "running":
                    return self._container
            except Exception:
                self._container = None

        import docker

        self._client = docker.from_env()
        self._container = self._client.containers.run(
            self.image,
            command="sleep infinity",
            detach=True,
            name=self.container_name,
            working_dir=self.root_dir,
            mem_limit=self.mem_limit,
            cpu_period=self.cpu_period,
            cpu_quota=self.cpu_quota,
            network_disabled=self.network_disabled,
            remove=True,
        )
        self._container.exec_run(f"mkdir -p {self.root_dir}")
        return self._container

    def close(self) -> None:
        if self._container is not None:
            try:
                self._container.stop(timeout=5)
            except Exception:
                try:
                    self._container.kill()
                except Exception:
                    pass
            self._container = None
        self._client = None

    def __del__(self) -> None:
        self.close()

    def execute(self, command: str, timeout: int = 30, cwd: str | None = None) -> ExecResult:
        container = self._ensure_container()
        work_dir = cwd or self.root_dir
        try:
            exit_code, output = container.exec_run(
                ["bash", "-c", command],
                workdir=work_dir,
                demux=True,
            )
        except Exception as exc:
            return ExecResult(
                stderr=f"Docker exec failed: {exc}",
                returncode=-1,
                success=False,
            )

        stdout_raw = (output[0] or b"").decode("utf-8", errors="replace") if output else ""
        stderr_raw = (output[1] or b"").decode("utf-8", errors="replace") if output else ""
        was_truncated = len(stdout_raw) > self._STDOUT_LIMIT or len(stderr_raw) > self._STDERR_LIMIT
        return ExecResult(
            stdout=stdout_raw[-self._STDOUT_LIMIT :],
            stderr=stderr_raw[-self._STDERR_LIMIT :],
            returncode=exit_code,
            success=exit_code == 0,
            truncated=was_truncated,
        )

    def run_python(self, code: str, timeout: int = 30, dependencies: list[str] | None = None) -> ExecResult:
        if dependencies:
            dep_cmd = f"pip install --quiet {' '.join(dependencies)} 2>/dev/null"
            self.execute(dep_cmd, timeout=60)
        cmd = f"python3 -c {_shell_quote(code)}"
        return self.execute(cmd, timeout=timeout)

    def ls(self, path: str = ".") -> list[FileInfo]:
        resolved = self._container_path(path)
        result = self.execute(
            f"find {_shell_quote(resolved)} -maxdepth 1 -printf '%y %s %T@ %P\\n' 2>/dev/null",
        )
        if not result.success:
            raise FileNotFoundError(f"Directory not found: {path}")
        items: list[FileInfo] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split(" ", 3)
            if len(parts) < 4 or not parts[3]:
                continue
            is_dir = parts[0] == "d"
            size = int(parts[1]) if not is_dir else 0
            modified = float(parts[2])
            items.append(
                FileInfo(
                    path=parts[3],
                    name=os.path.basename(parts[3]),
                    is_dir=is_dir,
                    size=size,
                    modified=modified,
                )
            )
        return sorted(items, key=lambda x: (not x.is_dir, x.name))

    def read(self, path: str) -> str:
        resolved = self._container_path(path)
        result = self.execute(f"cat {_shell_quote(resolved)}")
        if not result.success:
            raise FileNotFoundError(f"File not found: {path}")
        return result.stdout

    def write(self, path: str, content: str) -> WriteResult:
        resolved = self._container_path(path)
        dir_path = os.path.dirname(resolved)
        self.execute(f"mkdir -p {_shell_quote(dir_path)}")
        try:
            self._put_file(resolved, content.encode("utf-8"))
            return WriteResult(path=path)
        except Exception:
            return WriteResult(error="invalid_path")

    def edit(self, path: str, old: str, new: str) -> EditResult:
        try:
            content = self.read(path)
        except FileNotFoundError:
            return EditResult(error="file_not_found", old_found=False)
        if old not in content:
            return EditResult(old_found=False)
        content = content.replace(old, new, 1)
        self.write(path, content)
        return EditResult(path=path)

    def exists(self, path: str) -> bool:
        resolved = self._container_path(path)
        result = self.execute(f"test -e {_shell_quote(resolved)}")
        return result.success

    def mkdir(self, path: str) -> None:
        resolved = self._container_path(path)
        self.execute(f"mkdir -p {_shell_quote(resolved)}")

    def remove(self, path: str) -> bool:
        resolved = self._container_path(path)
        result = self.execute(f"rm -rf {_shell_quote(resolved)}")
        return result.success

    def glob(self, pattern: str) -> list[str]:
        result = self.execute(f"find {_shell_quote(self.root_dir)} -path {_shell_quote(pattern)} 2>/dev/null")
        if not result.stdout.strip():
            return []
        return [os.path.relpath(p, self.root_dir) for p in result.stdout.strip().splitlines() if p.strip()]

    def grep(self, pattern: str, path: str = ".", max_results: int = 50) -> list[dict[str, Any]]:
        resolved = self._container_path(path)
        result = self.execute(
            f"grep -rn --include='*' -m {max_results} {_shell_quote(pattern)} {_shell_quote(resolved)} 2>/dev/null"
        )
        results: list[dict[str, Any]] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 3:
                results.append(
                    {
                        "file": os.path.relpath(parts[0], self.root_dir),
                        "line": int(parts[1]) if parts[1].isdigit() else 0,
                        "content": parts[2][:200],
                    }
                )
        return results[:max_results]

    def _container_path(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return f"{self.root_dir}/{path}"

    def _put_file(self, container_path: str, data: bytes) -> None:
        container = self._ensure_container()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=os.path.basename(container_path))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        container.put_archive(os.path.dirname(container_path), buf)


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def is_docker_available() -> bool:
    """Check whether Docker daemon is reachable."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False
