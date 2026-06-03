# PyBot 小团队智能体 — 借鉴 OpenClaw / CowAgent 路线图

> 目标：方便（低运维）、能力强（工具/记忆/编排）、适合 3–15 人小团队日常使用。  
> 策略：**保留 PyBot 治理与持久资产优势**，从 OpenClaw/CowAgent **借产品形态**，不做全量重写。

---

## 原则


| 借                          | 不借（或后期）                         |
| -------------------------- | ------------------------------- |
| Workspace Markdown 人格/团队规则 | 原生 iOS/Android App（OpenClaw 量级） |
| Doctor 自检 + Web 运维台        | 120 个 npm 插件 monorepo           |
| IM 渠道安全（allowlist/pairing） | 完全迁移到 Node Gateway              |
| Deep Dream 式记忆蒸馏 UX        | 放弃 LangChain 自研 loop            |
| OpenClaw 配置/Skill 导入       | 1:1 复制 ClawHub                  |
| CowAgent 一体控制台思路           | COW 旧插件体系                       |
| 定时任务 / Cron 暴露             | LinkAI 云托管                      |


---

## Phase 1 — 团队开箱（当前 Sprint，已开始）

**借鉴**：OpenClaw `doctor` + CowAgent Workspace 文件 + 记忆运维

- `plan.md` 路线图
- `core/systems/runtime/system_doctor.py` — 结构化自检（LLM/渠道/记忆/插件/API Key）
- `GET /api/doctor` + `POST /api/doctor/bootstrap` — Web 可调
- Workspace：`TEAM.md` / `RULES.md` + CowAgent 别名 `AGENT.md`/`USER.md` + MEMORY 摘要注入
- 渠道 DM 策略：`dm_policy` + `allow_from` + pairing 码（OpenClaw 安全默认）
- OpenClaw 渠道导入扩展：feishu / dingtalk / wechat_claw
- `POST /api/memory/distill` — 手动触发记忆蒸馏（CowAgent `/memory rebuild`）
- Web `#/team` 团队控制台（Doctor + Workspace 模板 + 记忆蒸馏 + OpenClaw 导入）
- 测试：`test_system_doctor.py`、`test_channel_dm_policy.py`

---

## Phase 2 — 团队协作（2–3 周）

**借鉴**：OpenClaw 多 Agent 路由 + CowAgent Web 运维

- 渠道路由增强：按 `user_id` / 群 ID 绑定不同 thread / 子 Agent
- Gateway 设备配对 + IM pairing 统一到 Governance 面板
- Skills：OpenClaw repo 一键 import + PyHub 安装入口合并到 Integrations
- 记忆浏览 UI：daily / MEMORY.md / 蒸馏历史（CowAgent Web 记忆 API 思路）
- `pybot doctor --fix` CLI 与 Web bootstrap 对齐
- 团队共享 `workspace/TEAM.md` 版本化（git 友好说明）

---

## Phase 3 — 能力增强（1–2 月）

**借鉴**：CowAgent Deep Dream + OpenClaw Cron/Heartbeat

- Deep Dream 产品层：梦境日记 `memory/dreams/` + MEMORY.md 条数上限 ~30
- Cron 工具/Web UI：对接现有 `TaskScheduler` + `SCHEDULE.md`
- 知识 Wiki 轻量版：主题 Markdown + 简单图谱（不必 D3 全量）
- IM 流式中间消息：tool/thinking 进度推到飞书/企微
- 可选：Telegram/Slack channel（小团队常用）

---

## Phase 4 — 可选进阶

- Voice ASR/TTS（CowAgent 已有，OpenClaw Talk Mode）
- Live Canvas / A2UI（OpenClaw extension，PyBot 已有 ExecutionCanvas 策略层）
- ACP/Codex bundle MCP 合并
- 渠道 contract 测试矩阵（学 OpenClaw per-extension vitest）

---

## PyBot 应坚持的差异化（不对标放弃）

- Admin 模式 + capability gap 合成
- App Matrix + Shared Data Bus
- 治理/审批/沙箱中间件
- 运行时造 tool 并持久化
- PyFlow 工作流 + 2071+ pytest 工程基线

---

## 模块映射（实施参考）


| 借鉴来源      | OpenClaw / CowAgent | PyBot 落点                           |
| --------- | ------------------- | ---------------------------------- |
| Doctor    | `openclaw doctor`   | `system_doctor.py` + `/api/doctor` |
| Workspace | `~/cow/AGENT.md`    | `workspace/SOUL.md` + `TEAM.md`    |
| DM 安全     | `dmPolicy` pairing  | `channels/dm_policy.py`            |
| 记忆蒸馏      | Deep Dream          | `MemoryEngine.distill()` + Web     |
| 渠道        | extensions/*        | `openclaw_compat` + ChannelManager |
| 控制台       | web 9899            | `#/team` + `#/integrations`        |
| Skills    | ClawHub             | `openclaw/import` + PyHub          |


