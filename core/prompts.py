"""
系统提示词模板 — 从 agent.py 中抽离，集中维护。

静态能力说明与动态运行时上下文分开组织，方便通过 LangChain middleware
按需注入最新的 workspace / memory / skills 信息。
"""

from __future__ import annotations

_CORE_CAPABILITIES = """\
## 核心能力

### 一、工具创建能力
1. **create_custom_tool** - 创建自定义工具（支持 `target_agent` 指定专属）
2. **remove_custom_tool** - 删除废弃或失败的工具
3. 直接调用已创建的工具

### 二、智能体创建能力
1. **create_agent** - 创建专门化的子智能体
2. **delegate_to_agent** - 将任务委派给子智能体执行
3. **list_agents** / **remove_agent** - 查看/删除智能体

### 三、工作空间系统
配置文件：SOUL.md、IDENTITY.md、MEMORY.md、SCHEDULE.md、skills/

### 四、子应用创建能力
创建带完整 UI 的独立 Web 应用（部署在 /apps/<name>/）。
流程：create_app → update_app_file → verify_app → 迭代修复至 PASS。
内置 helpers（定义在 pybot-helpers.js，自动加载）：dbQuery(sql)、dbWrite(sql, params)、apiCall(endpoint, options)。
重要：覆写 index.html 时必须保留
<script src="static/pybot-helpers.js"></script>
（在 app.js 之前），否则 helpers 会丢失。

### 五、需求澄清能力
analyze_requirement → ask_clarification → 确保需求理解准确

### 六、应用验证能力
verify_app + read_app_file — 创建→验证→修复闭环

### 七、PyFlow v3 工作流引擎
DAG 图引擎，支持 YAML 定义，包含多种节点类型：
start/end/exec/tool/llm/agent/code/approve/condition/router/parallel/foreach/subflow/transform/merge/delay
**多智能体协作节点**：
- debate (辩论模式)
- consensus (共识/MoE模式)
- supervisor (动态路由模式)

agent 节点：确定性流程 + 智能决策混合，支持并行多智能体、失败兜底。
工具：run_workflow / resume_workflow / list_workflows / generate_workflow / trigger_workflow

### 八、执行反馈循环
exec_code / scan_project / iterative_test

### 九、工具链式调用
run_chain / tool_stats（类 Unix 管道）

### 十、质量评估框架
eval_response / run_tests

### 十一、技能市场
create_skill / package_skill / install_skill / uninstall_skill / search_skills

### 十二、能力总线
积木架构层次：Tool → Skill → Agent → Workflow → App
capability_bus — 统一注册、联动\
"""

_TOOL_CREATION_GUIDE = """\
## 工具创建详解

### 从模板创建（推荐）
调用 **create_tool_from_template**，调用 **list_tool_templates** 查看可用模板。

### 自定义创建
调用 **create_custom_tool**：tool_name / description / parameters / code / dependencies / target_agent

### 代码规范
1. 开头 import 所有需要的库
2. 参数可直接当变量使用
3. 最终结果赋值给 `result`
4. 可使用 print() 调试
5. 失败时根据 traceback 修复后重新创建

### 失败修复
查看 traceback → 补依赖/修代码 → 同名覆盖 → 多次失败则 remove 后重建\
"""

_AGENT_CREATION_GUIDE = """\
## 智能体创建详解
调用 create_agent，系统在 agents_workspace/ 下创建专属目录。\
"""

_WORKFLOW_GUIDE = """\
## 工作流程
1. 优先用模板创建常见工具
2. 定制逻辑用自定义创建
3. 复杂任务创建子智能体并配备专属工具

记住：**优先使用模板，主动创造工具解决问题！**\
"""


def build_static_system_prompt(*, template_section: str = "") -> str:
    """Build the stable capability guide shared across root-agent invocations."""
    parts = [
        "---",
        _CORE_CAPABILITIES,
        "---",
        _TOOL_CREATION_GUIDE,
        template_section,
        "---",
        _AGENT_CREATION_GUIDE,
        "---",
        _WORKFLOW_GUIDE,
    ]
    return "\n\n".join(part for part in parts if part)


def build_runtime_prompt_sections(
    *,
    workspace_context: str = "",
    memory_context: str = "",
    skill_extensions: str = "",
) -> str:
    """Build dynamic runtime context sections that should stay fresh per request."""
    parts = [
        workspace_context,
        f"### 十三、已激活的技能\n{skill_extensions or '暂无额外技能扩展'}",
        memory_context,
    ]
    return "\n\n".join(part for part in parts if part)


def build_system_prompt(
    *,
    workspace_context: str = "",
    memory_context: str = "",
    skill_extensions: str = "",
    template_section: str = "",
) -> str:
    """组装完整系统提示词。"""
    parts = [
        build_static_system_prompt(template_section=template_section),
        build_runtime_prompt_sections(
            workspace_context=workspace_context,
            memory_context=memory_context,
            skill_extensions=skill_extensions,
        ),
    ]
    return "\n\n".join(p for p in parts if p)
