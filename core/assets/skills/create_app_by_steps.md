---
name: create_app_by_steps
description: 使用原子文件工具（write_file等）手动创建子应用的 SOP
---

# 手动创建子应用指南

当用户要求你创建一个应用（App）时，你可以通过组合原子文件工具（如 `write_file`, `read_file`）来手动搭建。这能让你更精确地控制应用的代码。

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
完成上述三个文件的写入后，告诉用户应用已创建成功，并可以通过 `/apps/<app_name>/` 访问。
