"""Error analysis helpers for execution-loop tools."""

from __future__ import annotations

import re


def analyze_python_error(stderr: str) -> dict[str, str]:
    """Return a structured summary for common Python runtime failures."""
    analysis: dict[str, str] = {"type": "unknown", "suggestion": ""}

    if "ModuleNotFoundError" in stderr or "ImportError" in stderr:
        module_match = re.search(r"No module named '(\w+)'", stderr)
        module = module_match.group(1) if module_match else "unknown"
        analysis["type"] = "import_error"
        analysis["module"] = module
        analysis["suggestion"] = f"缺少依赖包 '{module}'，请先安装: pip install {module}"
    elif "SyntaxError" in stderr:
        analysis["type"] = "syntax_error"
        analysis["suggestion"] = "代码存在语法错误，请检查括号、引号、缩进是否正确"
    elif "TypeError" in stderr:
        analysis["type"] = "type_error"
        analysis["suggestion"] = "类型错误，请检查函数参数类型和变量类型是否匹配"
    elif "NameError" in stderr:
        analysis["type"] = "name_error"
        analysis["suggestion"] = "变量或函数未定义，请检查拼写和作用域"
    elif "FileNotFoundError" in stderr:
        analysis["type"] = "file_not_found"
        analysis["suggestion"] = "文件不存在，请检查文件路径是否正确"
    elif "PermissionError" in stderr:
        analysis["type"] = "permission_error"
        analysis["suggestion"] = "权限不足，请检查文件/目录权限"
    elif "ConnectionError" in stderr or "TimeoutError" in stderr:
        analysis["type"] = "network_error"
        analysis["suggestion"] = "网络连接问题，请检查 URL 和网络配置"
    elif "KeyError" in stderr:
        analysis["type"] = "key_error"
        analysis["suggestion"] = "字典键不存在，请先检查键是否存在或使用 .get() 方法"
    elif "IndexError" in stderr:
        analysis["type"] = "index_error"
        analysis["suggestion"] = "索引越界，请检查列表长度和索引值"

    return analysis


def analyze_javascript_error(stderr: str) -> dict[str, str]:
    """Return a structured summary for common JavaScript runtime failures."""
    analysis: dict[str, str] = {"type": "unknown", "suggestion": ""}

    if "Cannot find module" in stderr:
        analysis["type"] = "module_not_found"
        analysis["suggestion"] = "缺少 Node.js 模块，请先安装: npm install <module>"
    elif "SyntaxError" in stderr:
        analysis["type"] = "syntax_error"
        analysis["suggestion"] = "JavaScript 语法错误，请检查代码"
    elif "ReferenceError" in stderr:
        analysis["type"] = "reference_error"
        analysis["suggestion"] = "引用了未定义的变量或函数"
    elif "TypeError" in stderr:
        analysis["type"] = "type_error"
        analysis["suggestion"] = "类型错误，请检查变量类型和函数调用"

    return analysis
