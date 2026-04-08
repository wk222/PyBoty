---
name: create_app_by_steps
description: 使用原子文件工具（write_file等）手动创建子应用的 SOP
---

# 手动创建子应用指南

当用户要求你创建一个应用（App）时，你可以通过组合原子文件工具（如 `write_file`, `read_file`）来手动搭建。这能让你更精确地控制应用的代码。

但请注意：这是一条**底层 fallback 路线**，不是默认推荐路线。

默认推荐：
1. `build_app_iteratively`：一键生成 + 验证 + 自动修复
2. `create_app -> update_app_file -> verify_app -> test_app_api`
3. 只有在上面两条不适用时，才使用本 skill 的纯文件拼装方式

纯文件拼装的代价：
- 调用次数通常更多
- 容易漏掉 `pybot-helpers.js`、模板注入、metadata 维护
- 容易绕开 `AppManager` 的安全边界、JS 修复、验证闭环
- 做出来的结果更像“静态文件夹”，而不是“受平台托管的 app 资产”

## 目录结构规范
所有子应用必须存放在 `workspace/apps/<app_name>/` 目录下。
一个标准的子应用必须包含以下三个文件：
1. `app.json` (应用元数据配置)
2. `api.py` (后端逻辑)
3. `index.html` (前端页面)

## 步骤 1：创建 app.json
使用 `write_file` 工具写入 `workspace/apps/<app_name>/app.json`。
内容模板：
```json
{
  "name": "<app_name>",
  "display_name": "测试应用",
  "description": "应用描述",
  "mode": "chat",
  "tags": ["test"]
}
```

## 步骤 2：创建 api.py
使用 `write_file` 工具写入 `workspace/apps/<app_name>/api.py`。
你必须在这个文件中定义应用的后端逻辑。
例如：
```python
from typing import Dict, Any

def handle_request(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if action == "test":
        return {"status": "ok", "message": "Backend is working!"}
    return {"error": "Unknown action"}
```

## 步骤 3：创建 index.html
使用 `write_file` 工具写入 `workspace/apps/<app_name>/index.html`。
在这里编写前端页面，你可以使用内置的 JS 助手函数（如 `agentChat`, `agentCallTool`）。

## 步骤 4：验证
完成上述三个文件的写入后，不要直接结束。

还需要至少做这些事：
1. 检查 `index.html` 是否保留了 `static/pybot-helpers.js`
2. 为 `api.py` 添加 `test` action
3. 为前端添加 `window.runSelfTest = async () => { ... }`
4. 优先调用 `verify_app`
5. 如果存在后端，再调用 `test_app_api`

只有验证通过后，才算真正完成。
