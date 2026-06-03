"""Atomic file system operations for the agent."""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from pathlib import Path
from typing import Optional, Type
from langchain.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

from core.systems.context import WorkspaceViewEntry, WorkspaceViewService

logger = logging.getLogger(__name__)

_FILE_UNCHANGED_STUB = (
    "[FILE_UNCHANGED] 文件自上次读取后未修改 (mtime + size 一致)。"
    "\n路径: {path}\n视图: {view_label}\n行数: {line_count} | 大小: {file_size} bytes"
    "\n如需强制重读，请传入 force=true。"
)

ReadFileState = WorkspaceViewService

_INITIAL_WORKING_DIR = os.path.realpath(os.getcwd())


def _resolve_root(allowed_root: Optional[str]) -> str:
    """Return the absolute root directory used for path validation.

    Prefers the explicit ``allowed_root`` argument. When it is not provided we
    fall back to the working directory captured at module import time. This
    avoids subtle bugs where a concurrent tool changes ``os.getcwd()`` between
    operations and causes path validation to drift.
    """
    if allowed_root:
        return os.path.realpath(allowed_root)
    cwd = os.path.realpath(os.getcwd())
    if cwd != _INITIAL_WORKING_DIR:
        logger.debug(
            "_resolve_root: allowed_root missing and cwd drifted (initial=%s, current=%s); "
            "using initial working dir",
            _INITIAL_WORKING_DIR,
            cwd,
        )
    return _INITIAL_WORKING_DIR


def _check_path(path: str, allowed_root: Optional[str] = None) -> tuple[bool, str]:
    """Validate that path resolves inside the allowed root.

    Args:
        path: The file path to validate. Relative paths are resolved against
            ``allowed_root`` when provided, otherwise against the working
            directory captured at module import time.
        allowed_root: The root directory to enforce. When *None*, the cached
            initial working directory is used to keep behavior stable across
            concurrent tool calls.

    Returns:
        (True, resolved_absolute_path) on success.
        (False, error_message) when the path escapes the root.
    """
    root = _resolve_root(allowed_root)
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    resolved = os.path.realpath(candidate)
    
    # Standardize path separators and casing for robust Windows path checking
    norm_resolved = os.path.normpath(resolved).lower()
    norm_root = os.path.normpath(root).lower()
    
    if norm_resolved != norm_root and not norm_resolved.startswith(norm_root + os.sep):
        return False, f"❌ 路径越界: '{path}' 超出了允许的工作目录"
    return True, resolved


def _format_unchanged_stub(path: str, entry: WorkspaceViewEntry) -> str:
    view_label = "full"
    if entry.is_partial:
        total_lines = entry.total_lines or entry.line_count
        start = entry.offset + 1
        end = min(entry.offset + entry.limit, total_lines) if entry.limit > 0 else total_lines
        view_label = f"partial {start}-{end}/{total_lines}"
    return _FILE_UNCHANGED_STUB.format(
        path=path,
        view_label=view_label,
        line_count=entry.line_count,
        file_size=entry.file_size,
    )


class WriteFileInput(BaseModel):
    path: str = Field(description="文件的绝对或相对路径")
    content: str = Field(description="要写入的文件内容")


class WriteFileTool(BaseTool):
    name: str = "write_file"
    description: str = "写入或覆盖文件内容。常用于创建新脚本、配置文件或前端页面。"
    args_schema: Type[BaseModel] = WriteFileInput
    risk_level: str = "medium"
    allowed_root: Optional[str] = None
    _workspace_view: Optional[ReadFileState] = PrivateAttr(default=None)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def read_file_state(self) -> Optional[ReadFileState]:
        return self._workspace_view

    @read_file_state.setter
    def read_file_state(self, value: Optional[ReadFileState]) -> None:
        self._workspace_view = value

    def _run(self, path: str, content: str) -> str:
        try:
            ok, result = _check_path(path, self.allowed_root)
            if not ok:
                return result
            resolved = result
            os.makedirs(os.path.dirname(resolved), exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)
            if self.read_file_state is not None:
                self.read_file_state.invalidate(resolved)
            return f"✅ 成功写入文件: {path}"
        except Exception as e:
            return f"❌ 写入失败: {str(e)}"


class ReadFileInput(BaseModel):
    path: str = Field(description="要读取的文件路径")
    offset: int = Field(default=0, ge=0, description="起始行号（0 表示从头开始）")
    limit: int = Field(default=0, ge=0, description="最多读取行数（0 表示不限制）")
    force: bool = Field(default=False, description="强制重读，即使文件未修改")


class ReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = (
        "读取指定文件的内容。支持 offset/limit 做分段读取。"
        "如果文件自上次读取后未修改，将返回 FILE_UNCHANGED 提示以节省上下文；"
        "传入 force=true 可强制重读。"
    )
    args_schema: Type[BaseModel] = ReadFileInput
    risk_level: str = "low"
    allowed_root: Optional[str] = None
    _workspace_view: Optional[ReadFileState] = PrivateAttr(default=None)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def read_file_state(self) -> Optional[ReadFileState]:
        return self._workspace_view

    @read_file_state.setter
    def read_file_state(self, value: Optional[ReadFileState]) -> None:
        self._workspace_view = value

    def _run(
        self,
        path: str,
        offset: int = 0,
        limit: int = 0,
        force: bool = False,
    ) -> str:
        try:
            ok, result = _check_path(path, self.allowed_root)
            if not ok:
                return result
            resolved = result

            if not os.path.exists(resolved):
                return f"❌ 读取失败: 文件不存在 ({path})"

            try:
                stat = os.stat(resolved)
                current_mtime = stat.st_mtime
                current_size = stat.st_size
            except OSError as e:
                return f"❌ 读取失败: {e}"

            if not force and self.read_file_state is not None:
                entry = self.read_file_state.get_cached_view(
                    resolved,
                    current_mtime,
                    current_size,
                    offset=offset,
                    limit=limit,
                )
                if entry is not None:
                    return _format_unchanged_stub(path, entry)

            with open(resolved, "r", encoding="utf-8") as f:
                if offset > 0 or limit > 0:
                    lines = f.readlines()
                    total_lines = len(lines)
                    start = min(offset, total_lines)
                    end = min(start + limit, total_lines) if limit > 0 else total_lines
                    selected = lines[start:end]
                    content = "".join(selected)
                    is_partial = start > 0 or end < total_lines
                else:
                    content = f.read()
                    is_partial = False

            if self.read_file_state is not None:
                self.read_file_state.record_view(
                    resolved_path=resolved,
                    content=content,
                    mtime=current_mtime,
                    file_size=current_size,
                    offset=offset,
                    limit=limit,
                    is_partial=is_partial,
                    total_lines=total_lines if is_partial else 0,
                )

            if is_partial:
                header = f"[partial view] 行 {start+1}-{end}/{total_lines} of {path}\n"
                return header + content
            return content
        except Exception as e:
            return f"❌ 读取失败: {str(e)}"


class StrReplaceInput(BaseModel):
    path: str = Field(description="要编辑的文件路径")
    old_str: str = Field(description="要替换的原始字符串（必须在文件中唯一出现一次）")
    new_str: str = Field(description="替换后的新字符串")


class StrReplaceTool(BaseTool):
    name: str = "str_replace"
    description: str = (
        "在文件中进行精确字符串替换。old_str 必须在文件中唯一出现一次，"
        "否则操作失败以防止歧义修改。适合对已有文件进行局部定向编辑。"
    )
    args_schema: Type[BaseModel] = StrReplaceInput
    risk_level: str = "medium"
    allowed_root: Optional[str] = None
    _workspace_view: Optional[ReadFileState] = PrivateAttr(default=None)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def read_file_state(self) -> Optional[ReadFileState]:
        return self._workspace_view

    @read_file_state.setter
    def read_file_state(self, value: Optional[ReadFileState]) -> None:
        self._workspace_view = value

    def _run(self, path: str, old_str: str, new_str: str) -> str:
        try:
            ok, result = _check_path(path, self.allowed_root)
            if not ok:
                return result
            resolved = result

            if not os.path.exists(resolved):
                return f"❌ 替换失败: 文件不存在 ({path})"

            with open(resolved, "r", encoding="utf-8") as f:
                content = f.read()

            count = content.count(old_str)
            if count == 0:
                return "❌ 替换失败: 在文件中未找到指定字符串"
            if count > 1:
                return (
                    f"❌ 替换失败: 指定字符串在文件中出现了 {count} 次（期望唯一），"
                    "请提供更多上下文以消除歧义"
                )

            new_content = content.replace(old_str, new_str, 1)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(new_content)
            if self.read_file_state is not None:
                self.read_file_state.invalidate(resolved)
            return f"✅ 成功替换文件 {path} 中的指定字符串"
        except Exception as e:
            return f"❌ 替换失败: {str(e)}"


class ListDirectoryInput(BaseModel):
    path: str = Field(default=".", description="要列出内容的目录路径，默认为当前工作目录")
    depth: int = Field(
        default=1, ge=1, le=10,
        description="列出目录树的最大深度，默认为 1（仅列出直接子条目）",
    )


class ListDirectoryTool(BaseTool):
    name: str = "list_directory"
    description: str = (
        "列出目录中的文件和子目录，支持指定最大深度。"
        "在读取或写入文件前，可先用此工具探索目录结构。"
    )
    args_schema: Type[BaseModel] = ListDirectoryInput
    risk_level: str = "low"
    allowed_root: Optional[str] = None

    def _run(self, path: str = ".", depth: int = 1) -> str:
        try:
            ok, result = _check_path(path, self.allowed_root)
            if not ok:
                return result
            resolved = result

            if not os.path.exists(resolved):
                return f"❌ 列出失败: 路径不存在 ({path})"
            if not os.path.isdir(resolved):
                return f"❌ 列出失败: 路径不是目录 ({path})"

            lines: list[str] = [f"📁 {path}"]
            self._walk(resolved, depth, 1, lines)
            return "\n".join(lines)
        except Exception as e:
            return f"❌ 列出失败: {str(e)}"

    def _walk(
        self,
        current: str,
        max_depth: int,
        current_depth: int,
        lines: list[str],
    ) -> None:
        if current_depth > max_depth:
            return
        try:
            entries = sorted(os.scandir(current), key=lambda e: (not e.is_dir(), e.name))
        except PermissionError:
            lines.append("    " * current_depth + "⚠️  (权限不足)")
            return
        indent = "    " * current_depth
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                lines.append(f"{indent}📁 {entry.name}/")
                self._walk(entry.path, max_depth, current_depth + 1, lines)
            else:
                lines.append(f"{indent}📄 {entry.name}")


_MAX_GREP_RESULTS = 500
_MAX_GREP_LINE_LEN = 500


class GrepFilesInput(BaseModel):
    pattern: str = Field(description="正则表达式搜索模式（Python re 语法）")
    path: str = Field(default=".", description="搜索起始目录，默认为当前工作目录")
    include: str = Field(default="*", description="文件名 glob 过滤器，仅搜索匹配的文件（如 '*.py'、'*.ts'）")
    case_sensitive: bool = Field(default=True, description="是否区分大小写，默认区分")
    max_results: int = Field(default=200, ge=1, le=_MAX_GREP_RESULTS, description="最多返回的匹配行数，默认 200")


class GrepFilesTool(BaseTool):
    name: str = "grep_files"
    description: str = (
        "在目录下递归搜索文件内容，返回匹配正则表达式的行（含文件路径和行号）。"
        "支持文件名过滤（include 参数，如 '*.py'）、大小写控制和结果数量上限。"
        "适合快速定位代码、配置或日志中的特定内容。"
    )
    args_schema: Type[BaseModel] = GrepFilesInput
    risk_level: str = "low"
    allowed_root: Optional[str] = None

    def _run(
        self,
        pattern: str,
        path: str = ".",
        include: str = "*",
        case_sensitive: bool = True,
        max_results: int = 200,
    ) -> str:
        try:
            ok, result = _check_path(path, self.allowed_root)
            if not ok:
                return result
            search_root = result

            if not os.path.exists(search_root):
                return f"❌ 搜索失败: 路径不存在 ({path})"
            if not os.path.isdir(search_root):
                return f"❌ 搜索失败: 路径不是目录 ({path})"

            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                compiled = re.compile(pattern, flags)
            except re.error as exc:
                return f"❌ 无效的正则表达式: {exc}"

            cap = min(max_results, _MAX_GREP_RESULTS)
            matches: list[str] = []
            truncated = False
            effective_root = _resolve_root(self.allowed_root)
            anchor = Path(effective_root)

            for dirpath, dirnames, filenames in os.walk(search_root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for filename in sorted(filenames):
                    if filename.startswith("."):
                        continue
                    if not fnmatch.fnmatch(filename, include):
                        continue

                    file_path = os.path.join(dirpath, filename)
                    ok2, resolved2 = _check_path(file_path, effective_root)
                    if not ok2:
                        continue
                    try:
                        rel = str(Path(resolved2).relative_to(anchor))
                    except ValueError:
                        rel = file_path

                    try:
                        with open(resolved2, "r", encoding="utf-8", errors="replace") as fh:
                            for lineno, line in enumerate(fh, start=1):
                                if compiled.search(line):
                                    content = line.rstrip("\n")
                                    if len(content) > _MAX_GREP_LINE_LEN:
                                        content = content[:_MAX_GREP_LINE_LEN] + " …"
                                    matches.append(f"{rel}:{lineno}: {content}")
                                    if len(matches) >= cap:
                                        truncated = True
                                        break
                        if truncated:
                            break
                    except (OSError, PermissionError):
                        continue
                if truncated:
                    break

            if not matches:
                return f"（无匹配结果：pattern='{pattern}', include='{include}', path='{path}'）"

            output = "\n".join(matches)
            if truncated:
                output += f"\n\n⚠️  结果已截断，仅显示前 {cap} 条匹配（共可能更多）。请缩小搜索范围或增大 max_results。"
            return output
        except Exception as exc:
            return f"❌ 搜索失败: {exc}"


class GlobFilesInput(BaseModel):
    pattern: str = Field(description="Glob 匹配模式（如 '**/*.py'、'src/**/*.ts'）")
    path: str = Field(default=".", description="搜索起始目录，默认为当前工作目录")
    exclude: str = Field(default="", description="排除的文件名 glob 模式（如 '*.pyc'），留空则不排除")


class GlobFilesTool(BaseTool):
    name: str = "glob_files"
    description: str = (
        "在目录下按 glob 模式递归查找文件，返回匹配的相对路径列表（已排序）。"
        "支持 ** 通配符（如 '**/*.py' 匹配所有子目录下的 Python 文件）和排除模式。"
        "适合快速定位特定类型的文件或按目录结构筛选目标文件。"
    )
    args_schema: Type[BaseModel] = GlobFilesInput
    risk_level: str = "low"
    allowed_root: Optional[str] = None

    def _run(self, pattern: str, path: str = ".", exclude: str = "") -> str:
        try:
            ok, result = _check_path(path, self.allowed_root)
            if not ok:
                return result
            search_root = result

            if not os.path.exists(search_root):
                return f"❌ 查找失败: 路径不存在 ({path})"
            if not os.path.isdir(search_root):
                return f"❌ 查找失败: 路径不是目录 ({path})"

            effective_root = _resolve_root(self.allowed_root)
            anchor = Path(effective_root)
            search_path = Path(search_root)
            matched: list[str] = []

            for file_path in search_path.rglob(pattern):
                if not file_path.is_file():
                    continue
                parts = file_path.parts
                if any(p.startswith(".") for p in parts):
                    continue
                ok2, resolved2 = _check_path(str(file_path), effective_root)
                if not ok2:
                    continue
                resolved_path = Path(resolved2)
                try:
                    rel = str(resolved_path.relative_to(anchor))
                except ValueError:
                    rel = str(file_path)
                if exclude and (
                    fnmatch.fnmatch(file_path.name, exclude)
                    or fnmatch.fnmatch(rel, exclude)
                ):
                    continue
                matched.append(rel)

            if not matched:
                return f"（无匹配文件：pattern='{pattern}', path='{path}'）"

            matched.sort()
            return "\n".join(matched)
        except Exception as exc:
            return f"❌ 查找失败: {exc}"


def get_file_system_tools(
    allowed_root: Optional[str] = None,
    workspace_view: Optional[WorkspaceViewService] = None,
    read_file_state: Optional[ReadFileState] = None,
) -> list[BaseTool]:
    """返回所有原子化文件系统工具。

    Args:
        allowed_root: Optional directory path to use as the security boundary.
            Defaults to the process working directory at the time each tool
            call executes.
        workspace_view: Optional shared WorkspaceViewService for cross-tool
            file view tracking and deduplication.
        read_file_state: Optional shared ReadFileState for cross-tool file
            view tracking and deduplication.  When provided, ReadFileTool
            will return FILE_UNCHANGED stubs for unchanged files, and
            WriteFileTool / StrReplaceTool will invalidate the cache on writes.
    """
    if workspace_view is not None:
        state = workspace_view
    elif read_file_state is not None:
        state = read_file_state
    else:
        state = ReadFileState()

    write_tool = WriteFileTool(allowed_root=allowed_root)
    read_tool = ReadFileTool(allowed_root=allowed_root)
    replace_tool = StrReplaceTool(allowed_root=allowed_root)

    object.__setattr__(write_tool, "_workspace_view", state)
    object.__setattr__(read_tool, "_workspace_view", state)
    object.__setattr__(replace_tool, "_workspace_view", state)

    return [
        write_tool,
        read_tool,
        replace_tool,
        ListDirectoryTool(allowed_root=allowed_root),
        GrepFilesTool(allowed_root=allowed_root),
        GlobFilesTool(allowed_root=allowed_root),
    ]
