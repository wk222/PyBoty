"""
UV 环境管理器 (UvEnvManager)

管理多个独立的 Python UV 虚拟环境，每个环境可用于不同用途：
1. 创建/删除/列出环境
2. 在指定环境中安装/卸载包
3. 在指定环境中运行 Python 脚本
4. 查看环境详情（已安装包列表）
5. 持久化环境元数据到 envs.json
"""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UvEnvDefinition:
    name: str
    description: str = ""
    python_version: str = ""
    created_at: float = field(default_factory=time.time)
    packages: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class UvEnvManager:
    def __init__(self, envs_dir: str = "uv_envs"):
        self.envs_dir = envs_dir
        self.meta_file = os.path.join(envs_dir, "envs.json")
        self.envs: dict[str, UvEnvDefinition] = {}
        os.makedirs(envs_dir, exist_ok=True)
        self._load_meta()

    def _load_meta(self):
        if os.path.exists(self.meta_file):
            try:
                with open(self.meta_file, encoding="utf-8") as f:
                    data = json.load(f)
                for name, info in data.items():
                    self.envs[name] = UvEnvDefinition(**info)
            except Exception:
                self.envs = {}
        self._sync_with_disk()

    def _sync_with_disk(self):
        if not os.path.exists(self.envs_dir):
            return
        disk_envs = set()
        dirty = False
        for entry in os.scandir(self.envs_dir):
            if entry.is_dir() and not entry.name.startswith("."):
                venv_dir = os.path.join(entry.path, ".venv")
                if os.path.isdir(venv_dir):
                    disk_envs.add(entry.name)
                    if entry.name not in self.envs:
                        self.envs[entry.name] = UvEnvDefinition(
                            name=entry.name, description="(auto-discovered)", created_at=entry.stat().st_ctime
                        )
                        dirty = True
        stale = [n for n in self.envs if n not in disk_envs]
        for n in stale:
            del self.envs[n]
            dirty = True
        if dirty:
            self._save_meta()

    def _save_meta(self):
        data = {name: env.to_dict() for name, env in self.envs.items()}
        with open(self.meta_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _env_path(self, name: str) -> str:
        return os.path.abspath(os.path.join(self.envs_dir, name))

    def _venv_path(self, name: str) -> str:
        return os.path.join(self._env_path(name), ".venv")

    def _scripts_dir(self, name: str) -> str:
        folder = "Scripts" if os.name == "nt" else "bin"
        return os.path.join(self._venv_path(name), folder)

    def _pip_path(self, name: str) -> str:
        executable = "pip.exe" if os.name == "nt" else "pip"
        return os.path.join(self._scripts_dir(name), executable)

    def _python_path(self, name: str) -> str:
        executable = "python.exe" if os.name == "nt" else "python"
        return os.path.join(self._scripts_dir(name), executable)

    def _validate_name(self, name: str) -> str | None:
        if not name or not re.match(r"^[a-zA-Z0-9_-]{1,64}$", name):
            return "环境名称只能包含字母、数字、下划线和连字符，长度1-64"
        return None

    def create_env(
        self, name: str, description: str = "", python_version: str = "", tags: list[str] | None = None
    ) -> dict[str, Any]:
        err = self._validate_name(name)
        if err:
            return {"success": False, "error": err}
        if name in self.envs:
            return {"success": False, "error": f"环境 '{name}' 已存在"}

        env_path = self._env_path(name)
        os.makedirs(env_path, exist_ok=True)

        cmd = ["uv", "venv", self._venv_path(name)]
        if python_version:
            cmd.extend(["--python", python_version])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                shutil.rmtree(env_path, ignore_errors=True)
                return {"success": False, "error": f"创建虚拟环境失败: {result.stderr.strip()}"}
        except subprocess.TimeoutExpired:
            shutil.rmtree(env_path, ignore_errors=True)
            return {"success": False, "error": "创建超时"}
        except FileNotFoundError:
            shutil.rmtree(env_path, ignore_errors=True)
            return {"success": False, "error": "uv 未安装"}

        detected_py = ""
        try:
            py_result = subprocess.run(
                [self._python_path(name), "--version"], capture_output=True, text=True, timeout=10
            )
            if py_result.returncode == 0:
                detected_py = py_result.stdout.strip() or py_result.stderr.strip()
        except Exception:
            pass

        self.envs[name] = UvEnvDefinition(
            name=name, description=description, python_version=detected_py or python_version, tags=tags or []
        )
        self._save_meta()
        return {"success": True, "env": self.envs[name].to_dict()}

    def delete_env(self, name: str) -> dict[str, Any]:
        if name not in self.envs:
            return {"success": False, "error": f"环境 '{name}' 不存在"}
        env_path = self._env_path(name)
        try:
            shutil.rmtree(env_path, ignore_errors=True)
        except Exception as e:
            return {"success": False, "error": str(e)}
        del self.envs[name]
        self._save_meta()
        return {"success": True, "deleted": name}

    def list_envs(self) -> list[dict[str, Any]]:
        return [env.to_dict() for env in self.envs.values()]

    def get_env(self, name: str) -> dict[str, Any] | None:
        if name not in self.envs:
            return None
        info = self.envs[name].to_dict()
        info["packages"] = self._list_packages(name)
        info["disk_size"] = self._get_disk_size(name)
        return info

    def install_packages(self, name: str, packages: list[str]) -> dict[str, Any]:
        if name not in self.envs:
            return {"success": False, "error": f"环境 '{name}' 不存在"}
        if not packages:
            return {"success": False, "error": "请指定要安装的包"}

        err = self._validate_packages(packages)
        if err:
            return {"success": False, "error": err}

        cmd = ["uv", "pip", "install", "--python", self._python_path(name)] + packages
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()[-500:]}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "安装超时 (300s)"}
        except (FileNotFoundError, OSError) as e:
            return {"success": False, "error": f"执行失败: {e}"}

        self.envs[name].packages = self._list_packages(name)
        self._save_meta()
        return {"success": True, "installed": packages, "all_packages": self.envs[name].packages}

    def _validate_packages(self, packages: list[str]) -> str | None:
        if len(packages) > 20:
            return "一次最多安装/卸载20个包"
        for pkg in packages:
            if pkg.startswith("-"):
                return f"包名不能以 '-' 开头: {pkg}"
            if not re.match(r"^[a-zA-Z0-9_.@\[\]><=!~, -]+$", pkg):
                return f"包名不合法: {pkg}"
        return None

    def uninstall_packages(self, name: str, packages: list[str]) -> dict[str, Any]:
        if name not in self.envs:
            return {"success": False, "error": f"环境 '{name}' 不存在"}
        if not packages:
            return {"success": False, "error": "请指定要卸载的包"}

        err = self._validate_packages(packages)
        if err:
            return {"success": False, "error": err}

        cmd = ["uv", "pip", "uninstall", "--python", self._python_path(name)] + packages
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()[-500:]}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "卸载超时"}
        except (FileNotFoundError, OSError) as e:
            return {"success": False, "error": f"执行失败: {e}"}

        self.envs[name].packages = self._list_packages(name)
        self._save_meta()
        return {"success": True, "uninstalled": packages}

    MAX_TIMEOUT = 120

    def run_script(self, name: str, code: str, timeout: int = 60) -> dict[str, Any]:
        if name not in self.envs:
            return {"success": False, "error": f"环境 '{name}' 不存在"}

        timeout = min(max(timeout, 1), self.MAX_TIMEOUT)
        python = self._python_path(name)
        if not os.path.exists(python):
            return {"success": False, "error": "环境的 Python 解释器不存在"}

        try:
            result = subprocess.run(
                [python, "-c", code], capture_output=True, text=True, timeout=timeout, cwd=self._env_path(name)
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"执行超时 ({timeout}s)"}
        except (FileNotFoundError, OSError) as e:
            return {"success": False, "error": f"执行失败: {e}"}

    def run_file(self, name: str, filepath: str, timeout: int = 60) -> dict[str, Any]:
        if name not in self.envs:
            return {"success": False, "error": f"环境 '{name}' 不存在"}

        timeout = min(max(timeout, 1), self.MAX_TIMEOUT)
        python = self._python_path(name)
        abs_path = os.path.abspath(filepath)
        if not abs_path.endswith(".py"):
            return {"success": False, "error": "只能运行 .py 文件"}
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"文件不存在: {filepath}"}

        try:
            result = subprocess.run(
                [python, abs_path], capture_output=True, text=True, timeout=timeout, cwd=self._env_path(name)
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"执行超时 ({timeout}s)"}
        except (FileNotFoundError, OSError) as e:
            return {"success": False, "error": f"执行失败: {e}"}

    def update_env_meta(
        self, name: str, description: str | None = None, tags: list[str] | None = None
    ) -> dict[str, Any]:
        if name not in self.envs:
            return {"success": False, "error": f"环境 '{name}' 不存在"}
        if description is not None:
            self.envs[name].description = description
        if tags is not None:
            self.envs[name].tags = tags
        self._save_meta()
        return {"success": True, "env": self.envs[name].to_dict()}

    def _list_packages(self, name: str) -> list[str]:
        cmd = ["uv", "pip", "list", "--python", self._python_path(name), "--format", "columns"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return []
            lines = result.stdout.strip().split("\n")
            packages = []
            for line in lines[2:]:
                parts = line.split()
                if len(parts) >= 2:
                    packages.append(f"{parts[0]}=={parts[1]}")
            return packages
        except Exception:
            return []

    def _get_disk_size(self, name: str) -> str:
        env_path = Path(self._env_path(name))
        if not env_path.exists():
            return "unknown"
        total_bytes = 0
        try:
            for root, _, files in os.walk(env_path):
                for filename in files:
                    file_path = Path(root) / filename
                    try:
                        total_bytes += file_path.stat().st_size
                    except OSError:
                        continue
        except OSError:
            return "unknown"
        return self._format_size(total_bytes)

    def _format_size(self, total_bytes: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(total_bytes)
        for unit in units:
            if size < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)} B"
                return f"{size:.1f} {unit}"
            size /= 1024
        return "unknown"
