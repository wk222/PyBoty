"""LangChain tools for validating generated sub-applications."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from core.assets.apps.app_manager import AppManager
from core.assets.apps.app_manager_registry import get_shared_app_manager, set_shared_app_manager
from core.assets.apps.app_verifier_checks import (
    build_verdict,
    check_api,
    check_css,
    check_html,
    check_javascript,
    check_runtime_contract,
    check_ux,
    check_visual_ui,
    generate_fix_instructions,
    score_issues,
    summarize_issues,
)
from core.systems.runtime import safe_resolve

APP_ENTRY_FILE = "index.html"
APP_API_FILE = "api.py"
APP_JS_FILE = "static/app.js"
APP_CSS_FILE = "static/style.css"


def _is_valid_app_name(app_name: str) -> bool:
    return bool(app_name) and app_name.replace("_", "").replace("-", "").isalnum()


@dataclass(slots=True)
class AppVerificationService:
    """Application-facing verification service with filesystem boundaries."""

    app_manager: AppManager

    def verify_app(self, app_name: str, *, auto_fix: bool = True, llm: Any = None) -> dict[str, Any]:
        app_dir = self._resolve_app_dir(app_name)
        if app_dir is None:
            return {"success": False, "error": f"无效的应用名称: '{app_name}'"}
        if not app_dir.exists():
            return {"success": False, "error": f"应用 '{app_name}' 不存在"}

        html_content = self._read_text_if_exists(app_dir / APP_ENTRY_FILE)
        js_path = app_dir / APP_JS_FILE
        js_content = self._read_text_if_exists(js_path)
        css_content = self._read_text_if_exists(app_dir / APP_CSS_FILE)
        api_content = self._read_text_if_exists(app_dir / APP_API_FILE)

        issues: list[dict[str, Any]] = []
        if html_content is None:
            issues.append(
                {
                    "severity": "critical",
                    "category": "structure",
                    "message": "缺少 index.html 主页面",
                    "fix": "创建 index.html 作为应用入口",
                }
            )
        else:
            issues.extend(check_html(html_content))

        if js_content is not None:
            issues.extend(check_javascript(js_content, js_path))

        if css_content is None:
            issues.append(
                {
                    "severity": "warning",
                    "category": "style",
                    "message": "缺少 style.css 样式文件",
                    "fix": "创建基础样式文件",
                }
            )
        else:
            issues.extend(check_css(css_content))

        if api_content is not None:
            issues.extend(check_api(api_content))

        if html_content is not None and js_content is not None:
            issues.extend(check_runtime_contract(html_content, js_content))
            issues.extend(check_ux(html_content, js_content))
        elif html_content is not None:
            issues.extend(check_ux(html_content, ""))
            
        if llm is not None and html_content is not None:
            issues.extend(check_visual_ui(app_dir, llm=llm))

        score = score_issues(issues)
        result: dict[str, Any] = {
            "success": True,
            "app_name": app_name,
            "verdict": build_verdict(score),
            "score": score,
            "summary": summarize_issues(issues),
            "issues": issues,
        }

        if auto_fix and issues:
            result["fix_instructions"] = generate_fix_instructions(issues)

        return result

    def read_app_file(self, app_name: str, file_path: str) -> dict[str, Any]:
        app_dir = self._resolve_app_dir(app_name)
        if app_dir is None:
            return {"success": False, "error": f"无效的应用名称: '{app_name}'"}
        if not app_dir.exists():
            return {"success": False, "error": f"应用 '{app_name}' 不存在"}

        full_path = self._resolve_file_path(app_dir, file_path)
        if full_path is None:
            return {"success": False, "error": "路径越权"}
        if not full_path.exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        content = full_path.read_text(encoding="utf-8")
        return {
            "success": True,
            "app_name": app_name,
            "file": file_path,
            "content": content,
            "size": len(content),
            "lines": content.count("\n") + 1,
        }

    def _resolve_app_dir(self, app_name: str) -> Path | None:
        if not _is_valid_app_name(app_name):
            return None
        try:
            return safe_resolve(self.app_manager.apps_dir, app_name)
        except PermissionError:
            return None

    @staticmethod
    def _resolve_file_path(app_dir: Path, file_path: str) -> Path | None:
        try:
            return safe_resolve(app_dir, file_path)
        except PermissionError:
            return None

    @staticmethod
    def _read_text_if_exists(path: Path) -> str | None:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")


def get_app_verification_service() -> AppVerificationService:
    """Return the shared verifier service used by app tools."""
    return AppVerificationService(get_shared_app_manager())


def set_verifier_app_manager(manager: AppManager) -> None:
    """Backward-compatible wrapper around the shared AppManager registry."""
    set_shared_app_manager(manager)


class VerifyAppInput(BaseModel):
    app_name: str = Field(description="要验证的应用名称")
    auto_fix: bool = Field(description="是否在发现问题后自动生成修复建议", default=True)


class VerifyAppTool(BaseTool):
    name: str = "verify_app"
    description: str = """验证子应用的代码质量，检查常见问题。

这是 App Runtime 分支里的正式验证节点，应该和 `create_app` / `update_app_file` /
`build_app_iteratively` 组成“生成 -> 验证 -> 修复”的闭环，而不是被裸 `write_file`
流程绕过去。

**验证内容**：
1. HTML 结构完整性（标签闭合、必要元素）
2. JavaScript 语法检查（通过 Node.js）
3. CSS 引用完整性
4. API 调用一致性
5. 响应式设计检查
6. 用户体验要素（加载状态、空状态、错误处理）
7. 视觉UI布局检查（如果支持 VLM 和 Playwright）

**何时使用**：
- 创建或更新子应用后，验证代码质量
- 用户反馈应用有问题时，诊断根因
- 作为"创建→验证→修复"闭环的验证步骤

**返回**：问题列表 + 严重程度 + 修复建议
"""
    args_schema: type[BaseModel] = VerifyAppInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    llm: Any = Field(default=None, exclude=True)

    def _run(self, app_name: str, auto_fix: bool = True) -> str:
        result = get_app_verification_service().verify_app(app_name, auto_fix=auto_fix, llm=self.llm)
        return json.dumps(result, ensure_ascii=False, indent=2)


class ReadAppFileInput(BaseModel):
    app_name: str = Field(description="应用名称")
    file_path: str = Field(description="文件路径 (如 'index.html', 'static/app.js', 'static/style.css', 'api.py')")


class ReadAppFileTool(BaseTool):
    name: str = "read_app_file"
    description: str = """读取子应用的文件内容，用于审查和修复。

优先用它来检查托管 app 内的文件状态；它属于 App Runtime 分支，语义上比通用 `read_file`
更贴近“修 app”这条链。

常用文件:
- index.html: 主页面
- static/app.js: JavaScript 代码
- static/style.css: 样式表
- api.py: 后端 API 处理器

在修复应用问题之前，先读取现有代码以了解当前状态。
"""
    args_schema: type[BaseModel] = ReadAppFileInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, app_name: str, file_path: str) -> str:
        result = get_app_verification_service().read_app_file(app_name, file_path)
        return json.dumps(result, ensure_ascii=False)


def get_app_verifier_tools() -> list[BaseTool]:
    return [
        VerifyAppTool(),
        ReadAppFileTool(),
    ]
