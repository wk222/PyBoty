---
name: create_tool_sop
description: 创建工具的指南与最佳实践（包含模板使用和自定义工具编写）
---

# 创建工具指南

当用户需要你创建一个工具时，请优先使用预制的模板工具。如果模板无法满足需求，再使用 `create_custom_tool` 编写自定义 Python 代码。

## 1. 使用模板工具
调用 `create_tool_from_template` 工具，并指定 `template_name`。

**可用模板列表:**
- **http_get** — HTTP GET请求
- **http_post** — HTTP POST请求
- **web_scraper** — 网页内容提取(去HTML标签)
- **web_search** — DuckDuckGo搜索引擎
- **read_file** — 读取文件
- **write_file** — 写入文件
- **list_directory** — 列出目录
- **run_python** — 执行Python代码
- **json_processor** — JSON解析/过滤
- **csv_reader** — CSV读取
- **text_summarizer** — 文本统计分析
- **calculator** — 数学计算器
- **datetime_tool** — 日期时间工具
- **url_info** — URL元信息获取

## 2. 编写自定义工具 (create_custom_tool)
如果必须从头编写代码，请遵循以下规范：

### 参数定义 (parameters)
必须是一个 JSON 数组，例如：
```json
[
  {"name": "radius", "type": "float", "description": "圆的半径", "default": null},
  {"name": "unit", "type": "str", "description": "单位", "default": "cm"}
]
```
支持的类型：`str`, `int`, `float`, `bool`, `list`, `dict`。

### 依赖声明 (dependencies)
需要的第三方 Python 包列表（如 `['requests', 'beautifulsoup4']`）。不需要写内置模块。

### 代码编写规范 (code)
- **输入参数**：直接使用参数名作为变量。
- **返回值**：必须将最终结果赋值给 `result` 变量。
- **日志输出**：可以使用 `print()` 函数输出日志。
- **隔离环境**：代码将在独立的 `uv` 虚拟环境中运行，请务必在代码开头 `import` 你在 dependencies 中声明的库。

**代码示例:**
```python
import requests
## 3. 与创建应用 (App) 的区别
- **工具 (Tool)** 是 PyBot 自身的能力扩展（如：`web_search`, `read_file`）。
- **应用 (App)** 是独立的 Web 界面和后端（如：`travel_planner`, `bili-hot-analyzer`）。
- **千万不要** 在创建 App 的过程中尝试使用 `create_custom_tool` 来实现 App 的业务逻辑。App 的业务逻辑应写在 App 目录下的 `api.py` 中。

## 4. 修复与更新已有工具
- 如果你需要修复或更新一个已经存在的自定义工具，**请务必使用原有的工具名称 (tool_name) 重新调用 `create_custom_tool`**。系统会自动覆盖更新原有工具。
- **严禁** 在原名称后添加 `_fixed` 或 `_v2` 等后缀来创建新工具。这会导致工具库冗余并引起同能工具冲突。如果确实改了名字，必须调用 `remove_custom_tool` 删除旧工具。

