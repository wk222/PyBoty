"""Pure verification checks for generated PyBot sub-apps."""

from __future__ import annotations

import json
import base64

def check_visual_ui(app_dir: Path, llm: Any = None) -> list[dict[str, str]]:
    """Use Playwright and a Vision-capable LLM to verify the app's visual layout."""
    issues: list[dict[str, str]] = []
    if llm is None:
        return issues
        
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        issues.append(
            issue(
                "warning",
                "visual",
                "无法执行视觉测试：未安装 playwright。请运行 `pip install playwright && playwright install`。",
                "安装 playwright 以启用 VLM 视觉验证",
            )
        )
        return issues

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # File URL to the app's index.html
            file_url = f"file://{app_dir.absolute()}/index.html"
            page.goto(file_url, wait_until="networkidle", timeout=10000)
            
            # Take screenshot
            screenshot_bytes = page.screenshot(type="jpeg", quality=80)
            base64_image = base64.b64encode(screenshot_bytes).decode("utf-8")
            browser.close()
            
        # Call VLM
        from langchain_core.messages import HumanMessage
        
        prompt = (
            "You are an expert UI/UX designer and frontend QA engineer. "
            "Analyze the provided screenshot of a web application for visual bugs. "
            "Look for overlapping text, cut-off elements, unstyled raw HTML, broken layouts, or illegible contrast. "
            "If the UI looks reasonable and usable, return an empty JSON array: []. "
            "If there are issues, return a JSON array of objects with keys: 'severity' (warning or critical), 'category' (visual), 'message', and 'fix'. "
            "Do not include markdown wrappers, just raw JSON."
        )
        
        msg = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                },
            ]
        )
        
        response = llm.invoke([msg])
        content = str(response.content).strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
            
        vlm_issues = json.loads(content.strip())
        if isinstance(vlm_issues, list):
            for i in vlm_issues:
                issues.append(issue(i.get("severity", "warning"), "visual", i.get("message", "Visual bug"), i.get("fix", "Adjust CSS/HTML")))
                
    except Exception as e:
        issues.append(
            issue(
                "warning",
                "visual",
                f"视觉测试执行失败: {e}",
                "检查 Playwright 配置或 VLM API 连通性",
            )
        )
        
    return issues


HELPER_FUNCTION_MARKERS = ("apiCall(", "dbQuery(", "dbWrite(")
ASYNC_CALL_MARKERS = ("fetch(", "apiCall(", "dbQuery(")
EVENT_MARKERS = ("addEventListener", "onclick", "DOMContentLoaded")
LOADING_PATTERNS = ("loading", "spinner", "skeleton", "加载中", "Loading")
EMPTY_PATTERNS = ("暂无", "没有数据", "no data", "empty", "空空如也")
ERROR_PATTERNS = ("error", "错误", "失败", "alert(", "toast")
SEVERITY_PENALTIES = {"critical": 20, "warning": 10, "info": 3}


def _fix_broken_regex_literals(js: str, js_path: Path) -> tuple[str, bool]:
    """Detect and auto-fix regex literals broken by raw newlines.

    Returns the (possibly fixed) JS content and whether a fix was applied.
    """
    from core.assets.tools.tool_arg_repair import _repair_js_regex

    fixed = _repair_js_regex(js)
    if fixed != js:
        js_path.write_text(fixed, encoding="utf-8")
        return fixed, True
    return js, False


def issue(severity: str, category: str, message: str, fix: str) -> dict[str, str]:
    """Build a JSON-serializable issue payload."""
    return {
        "severity": severity,
        "category": category,
        "message": message,
        "fix": fix,
    }


def check_html(html: str) -> list[dict[str, str]]:
    """Validate the basic structure and asset references in HTML."""
    issues: list[dict[str, str]] = []
    normalized = html.lower()

    if "<!doctype html>" not in normalized:
        issues.append(
            issue(
                "warning",
                "html",
                "缺少 <!DOCTYPE html> 声明",
                "在文件开头添加 <!DOCTYPE html>",
            )
        )

    if "<meta charset" not in normalized:
        issues.append(
            issue(
                "warning",
                "html",
                "缺少字符编码声明",
                '添加 <meta charset="UTF-8">',
            )
        )

    if '<meta name="viewport"' not in normalized:
        issues.append(
            issue(
                "warning",
                "html",
                "缺少 viewport meta 标签，移动端可能显示异常",
                '添加 <meta name="viewport" content="width=device-width, initial-scale=1.0">',
            )
        )

    if "<title>" not in normalized:
        issues.append(issue("info", "html", "缺少页面标题", "添加 <title>应用名称</title>"))

    open_tags = re.findall(r"<(div|section|main|article|header|footer|nav|form|table|ul|ol)\b", html, re.IGNORECASE)
    close_tags = re.findall(r"</(div|section|main|article|header|footer|nav|form|table|ul|ol)>", html, re.IGNORECASE)
    if len(open_tags) != len(close_tags):
        issues.append(
            issue(
                "critical",
                "html",
                f"HTML 标签可能未正确闭合 (开标签: {len(open_tags)}, 闭标签: {len(close_tags)})",
                "检查所有 HTML 标签是否正确闭合",
            )
        )

    if "style.css" not in html and "<style>" not in normalized:
        issues.append(
            issue(
                "warning",
                "html",
                "未引用 CSS 样式文件，也没有内联样式",
                '添加 <link rel="stylesheet" href="static/style.css">',
            )
        )

    if "app.js" not in html and "<script>" not in normalized:
        issues.append(
            issue(
                "warning",
                "html",
                "未引用 JavaScript 文件",
                '添加 <script src="static/app.js"></script>',
            )
        )

    return issues


def check_javascript(js: str, js_path: Path) -> list[dict[str, str]]:
    """Validate JavaScript syntax and common frontend risks."""
    issues: list[dict[str, str]] = []

    js, regex_fixed = _fix_broken_regex_literals(js, js_path)

    if regex_fixed:
        issues.append(
            issue(
                "warning",
                "javascript",
                "检测到正则表达式中包含原始换行符（JSON 反序列化导致），已自动修复",
                "在 JSON 中使用 \\\\n 代替 \\n 来表示正则中的换行匹配",
            )
        )

    try:
        result = subprocess.run(
            ["node", "--check", str(js_path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip()
            line_match = re.search(r":(\d+)", error_msg)
            line_info = f" (第{line_match.group(1)}行)" if line_match else ""
            issues.append(
                issue(
                    "critical",
                    "javascript",
                    f"JavaScript 语法错误{line_info}: {error_msg[:200]}",
                    f"修复 JS 语法错误{line_info}",
                )
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if not any(marker in js for marker in EVENT_MARKERS) and len(js) > 200:
        issues.append(
            issue(
                "info",
                "javascript",
                "没有检测到事件监听器，页面可能缺少交互性",
                "添加用户交互事件处理",
            )
        )

    if (
        any(marker in js for marker in ASYNC_CALL_MARKERS)
        and "catch" not in js
        and ".catch" not in js
        and "try" not in js
    ):
        issues.append(
            issue(
                "warning",
                "javascript",
                "存在异步调用但没有错误处理",
                "为 fetch/API 调用添加 .catch() 或 try/catch 错误处理",
            )
        )

    if "innerHTML" in js and "textContent" not in js:
        issues.append(
            issue(
                "info",
                "javascript",
                "使用了 innerHTML，注意 XSS 风险。对于纯文本内容建议使用 textContent",
                "对不含 HTML 的内容使用 textContent 替代 innerHTML",
            )
        )

    return issues


def check_css(css: str) -> list[dict[str, str]]:
    """Check whether the generated CSS is substantial and responsive."""
    issues: list[dict[str, str]] = []

    if len(css.strip()) < 50:
        issues.append(issue("warning", "css", "CSS 内容很少，UI 可能缺乏样式", "添加基础布局和排版样式"))

    if "@media" not in css:
        issues.append(
            issue(
                "info",
                "css",
                "没有媒体查询，移动端适配可能不佳",
                "添加 @media (max-width: 768px) 响应式断点",
            )
        )

    return issues


def check_api(api_code: str) -> list[dict[str, str]]:
    """Validate backend API snippets exposed by generated apps."""
    issues: list[dict[str, str]] = []

    try:
        compile(api_code, "<api.py>", "exec")
    except SyntaxError as exc:
        issues.append(
            issue(
                "critical",
                "api",
                f"api.py 语法错误 (第{exc.lineno}行): {exc.msg}",
                f"修复 api.py 第{exc.lineno}行的语法错误",
            )
        )

    if "result" not in api_code:
        issues.append(
            issue(
                "critical",
                "api",
                "api.py 没有设置 result 变量，将无法返回数据",
                "确保 api.py 最终将返回值赋给 result 变量",
            )
        )

    if "try" not in api_code and "except" not in api_code:
        issues.append(issue("warning", "api", "api.py 缺少异常处理", "添加 try/except 块处理可能的运行时错误"))

    return issues


def check_runtime_contract(html: str, js: str) -> list[dict[str, str]]:
    """Ensure helper contracts between HTML and JS stay consistent."""
    if any(marker in js for marker in HELPER_FUNCTION_MARKERS) and "pybot-helpers.js" not in html:
        return [
            issue(
                "critical",
                "runtime",
                "app.js 使用了 apiCall/dbQuery/dbWrite 但 index.html 未引入 pybot-helpers.js",
                '在 index.html 的 <script src="static/app.js"> 之前添加 '
                '<script src="static/pybot-helpers.js"></script>',
            )
        ]
    return []


def check_ux(html: str, js: str) -> list[dict[str, str]]:
    """Look for basic UX affordances around async and data-heavy screens."""
    issues: list[dict[str, str]] = []
    combined = f"{html}\n{js}".lower()

    has_loading_state = any(pattern.lower() in combined for pattern in LOADING_PATTERNS)
    has_async_call = any(marker in js for marker in ASYNC_CALL_MARKERS)
    if not has_loading_state and has_async_call:
        issues.append(
            issue("info", "ux", "存在异步数据加载但没有加载状态提示", "添加加载中状态（如 spinner 或骨架屏）")
        )

    if not any(pattern.lower() in combined for pattern in EMPTY_PATTERNS) and (
        "foreach" in combined or "map(" in combined or "table" in html.lower()
    ):
        issues.append(issue("info", "ux", "数据列表没有空状态提示", "当数据为空时显示友好的空状态提示"))

    if not any(pattern.lower() in combined for pattern in ERROR_PATTERNS) and any(
        marker in js for marker in ("fetch(", "apiCall(")
    ):
        issues.append(
            issue(
                "info",
                "ux",
                "没有检测到用户可见的错误提示 UI",
                "添加错误提示组件（如 toast/alert），让用户了解操作结果",
            )
        )

    return issues


def summarize_issues(issues: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregate issue counts by severity."""
    counts = Counter(issue["severity"] for issue in issues)
    return {
        "critical": counts.get("critical", 0),
        "warning": counts.get("warning", 0),
        "info": counts.get("info", 0),
        "total": len(issues),
    }


def score_issues(issues: list[dict[str, Any]]) -> int:
    """Return a simple quality score based on issue severities."""
    score = 100
    for current in issues:
        score -= SEVERITY_PENALTIES.get(str(current["severity"]), 0)
    return max(0, score)


def build_verdict(score: int) -> str:
    """Convert a score into a high-level verdict."""
    if score >= 80:
        return "PASS"
    if score >= 50:
        return "NEEDS_IMPROVEMENT"
    return "FAIL"


def generate_fix_instructions(issues: list[dict[str, Any]]) -> str:
    """Produce prioritized fix guidance for the agent loop."""
    critical = [item for item in issues if item["severity"] == "critical"]
    warnings = [item for item in issues if item["severity"] == "warning"]

    instructions: list[str] = []
    if critical:
        instructions.append("### 必须修复（Critical）")
        for index, current in enumerate(critical, 1):
            instructions.append(f"{index}. [{current['category']}] {current['message']}")
            instructions.append(f"   修复: {current['fix']}")

    if warnings:
        instructions.append("\n### 建议修复（Warning）")
        for index, current in enumerate(warnings, 1):
            instructions.append(f"{index}. [{current['category']}] {current['message']}")
            instructions.append(f"   修复: {current['fix']}")

    if critical:
        instructions.append("\n请使用 update_app_file 工具修复以上问题，然后再次调用 verify_app 验证。")

    return "\n".join(instructions)
