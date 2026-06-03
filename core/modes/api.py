"""身份层（Layer 3）统一 API 入口。

本模块是 web/ 层消费 core/modes/ 的唯一推荐入口点，汇聚：
  - ModeProfile         — 职责角色（assistant / app_matrix / admin）
  - ExecutionCanvas     — 资源策略（focused / balanced / deep）
  - AgentControlPolicy  — 底层能力控制策略

设计约束
--------
- 此文件只 re-export，不包含新逻辑
- web/ 层只从这里 import，不直接 import canvas.py / system_model.py / agent_control.py
- 调用链文档：Session Spine → ModeProfile → ExecutionCanvas → AgentControlPolicy

完整调用链
----------
1. 用户请求到达 web/routers/chat.py
2. chat.py 从 ConversationStore 读取 thread 的 canvas 名称
3. AgentPool.get_or_create_mode() 调用 get_canvas_profile(canvas) 获取 CanvasProfile
4. CanvasProfile.to_llm_overrides()   → 注入 LLM 客户端参数
5. CanvasProfile.to_control_overrides() → merge 进 AgentControlPolicy
6. Session Spine 监听 CANVAS_CHANGED 事件 → focused 模式触发上下文立即压缩
7. chat_stream 按 canvas.digest_interval 触发 MemoryPipeline 蒸馏
"""

from __future__ import annotations

# ─── 身份角色（Role Identity）──────────────────────────────────────────────────
from core.modes.profile import (  # noqa: F401
    ModeProfile,
    resolve_mode_profile,
    list_mode_profiles,
)

# ─── 资源策略（Resource Strategy）────────────────────────────────────────────────
from core.modes.canvas import (  # noqa: F401
    CanvasProfile,
    get_canvas_profile,
    list_canvas_profiles,
    CANVAS_NAMES,
    DEFAULT_CANVAS,
)

# ─── 能力控制（Capability Control）──────────────────────────────────────────────
from core.systems.governance.agent_control import AgentControlPolicy  # noqa: F401

# ─── 便捷组合 API ────────────────────────────────────────────────────────────────

def get_session_config(
    *,
    root_mode: str | None = None,
    canvas: str | None = None,
) -> dict:
    """返回会话级完整配置：角色描述 + LLM 覆盖 + agent_control 覆盖。

    用于 web/ 层一次性拉取本次请求所需的所有身份层配置。

    Example
    -------
    >>> cfg = get_session_config(root_mode="assistant", canvas="focused")
    >>> cfg["mode_label"]          # "助手模式"
    >>> cfg["llm_overrides"]       # {"temperature": 0.3, "max_tokens": 1500}
    >>> cfg["control_overrides"]   # {"max_subagent_depth": 1, ...}
    >>> cfg["digest_interval"]     # 30
    """
    mode = resolve_mode_profile(root_mode or "assistant")
    canvas_profile = get_canvas_profile(canvas)

    return {
        "mode_name": mode.name,
        "mode_label": mode.label,
        "canvas_name": canvas_profile.name,
        "canvas_label": canvas_profile.label,
        "canvas_description": canvas_profile.description,
        "llm_overrides": canvas_profile.to_llm_overrides(),
        "control_overrides": canvas_profile.to_control_overrides(),
        "digest_on_every_turn": canvas_profile.digest_on_every_turn,
        "digest_interval": canvas_profile.digest_interval,
    }
