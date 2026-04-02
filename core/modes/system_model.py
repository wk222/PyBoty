"""Canonical executable model for PyBot's public concepts and code domains.

Design Philosophy
=================

PyBot is a **persistent admin runtime** — not a chatbot, not a framework, but
a long-lived agent that owns goals, delegates work, creates capabilities, and
evolves itself while staying governable.

Three principles drive every architectural decision:

1. **Three root modes, one identity**
   PyBot has exactly three operating modes — assistant, app-matrix, and admin —
   forming a progression from reactive helper to autonomous admin. All three
   share the same runtime foundation; they differ only in autonomy level and
   primary responsibilities. No extra "product layers" should be invented.

2. **Five product concepts, nothing more**
   Everything the user sees maps to tools, skills, agents, workflows, or apps.
   Supporting systems (governance, memory, middleware, observability, MCP, RAG)
   strengthen these concepts but are never presented as first-class products.
   Before adding a new top-level concept, ask: can it be a runtime, backend,
   profile, or registry under one of the five?

3. **Capability-first, surface-last**
   The core runtime produces capabilities; delivery surfaces (web, API, CLI,
   channels) merely expose them. Code organization follows this inside-out order:
   systems/ (foundation) → assets/ (domain objects) → modes/ (identity) → web/
   (consumers). Each layer depends only on layers below it.

Physical layout (post-migration):
    core/systems/runtime/      — config, paths, errors, events, model, bootstrap
    core/systems/governance/   — approvals, guardrails, sandbox, agent control
    core/systems/memory/       — semantic memory, scoring, knowledge retrieval
    core/systems/knowledge/    — document pipeline, vector store, embedding
    core/systems/middleware/   — agent/tool middleware chain
    core/systems/execution/    — code execution sandbox and analysis
    core/systems/eval/         — evaluation framework and storage
    core/systems/context/      — context windowing strategies
    core/systems/integration/  — channels, MCP, external content, PyHub
    core/systems/bus/          — capability bus runtime
    core/assets/tools/         — tool creation, runtime, storage, templates
    core/assets/skills/        — skill loading, registry, HTTP backends
    core/assets/agents/        — agent definition, delegation, profiles
    core/assets/apps/          — app manager, brain planner, orchestration
    core/assets/workflows/     — DAG execution, scheduling, pause/resume
    core/modes/                — root mode profiles and admin runtime
    web/                       — FastAPI surfaces (consumer layer)

Backward compatibility: every moved module keeps a one-line stub at its original
core/foo.py path, so from core.foo import X continues to work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _alias_key(value: str | None) -> str:
    text = (value or "").strip().lower().replace("_", " ").replace("-", " ")
    return " ".join(text.split())


@dataclass(frozen=True)
class RootModeDescriptor:
    name: str
    label: str
    summary: str
    autonomy_level: str
    primary_responsibilities: tuple[str, ...]
    typical_outputs: tuple[str, ...]
    enabled_capabilities: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "summary": self.summary,
            "autonomy_level": self.autonomy_level,
            "primary_responsibilities": list(self.primary_responsibilities),
            "typical_outputs": list(self.typical_outputs),
            "enabled_capabilities": list(self.enabled_capabilities),
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class ProductConceptDescriptor:
    name: str
    label: str
    question: str
    responsibility: str
    capability_examples: tuple[str, ...]
    typical_modules: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "question": self.question,
            "responsibility": self.responsibility,
            "capability_examples": list(self.capability_examples),
            "typical_modules": list(self.typical_modules),
        }


@dataclass(frozen=True)
class SupportingSystemDescriptor:
    name: str
    label: str
    purpose: str
    responsibilities: tuple[str, ...]
    strengthens_concepts: tuple[str, ...]
    typical_modules: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "purpose": self.purpose,
            "responsibilities": list(self.responsibilities),
            "strengthens_concepts": list(self.strengthens_concepts),
            "typical_modules": list(self.typical_modules),
        }


@dataclass(frozen=True)
class InternalDomainDescriptor:
    name: str
    label: str
    purpose: str
    examples: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "purpose": self.purpose,
            "examples": list(self.examples),
        }


@dataclass(frozen=True)
class PackageTargetDescriptor:
    name: str
    label: str
    path: str
    purpose: str
    migration_scope: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "path": self.path,
            "purpose": self.purpose,
            "migration_scope": list(self.migration_scope),
        }


_ROOT_MODES: tuple[RootModeDescriptor, ...] = (
    RootModeDescriptor(
        name="assistant",
        label="人类助手模式",
        summary="默认的人类协作入口，负责即时对话、分析、执行和工具协作。",
        autonomy_level="interactive",
        primary_responsibilities=(
            "回答用户问题",
            "调用工具完成分析与执行",
            "在必要时创建可复用能力",
        ),
        typical_outputs=("answers", "tool runs", "small reusable assets"),
        enabled_capabilities=("interactive_chat",),
        aliases=("assistant", "人类助手", "人类助手模式", "通用协作助手"),
    ),
    RootModeDescriptor(
        name="app_matrix",
        label="应用矩阵智能体",
        summary="面向特定业务线的管家，沙盒隔离，只能访问绑定的应用矩阵、独立知识库和专属数据。",
        autonomy_level="orchestration",
        primary_responsibilities=(
            "管理和调度绑定的应用矩阵",
            "在专属知识库中检索和存储信息",
            "执行特定业务线的自动化工作流",
        ),
        typical_outputs=("app pipelines", "domain-specific answers", "matrix coordination"),
        enabled_capabilities=("interactive_chat", "durable_goal_loop", "app_orchestration", "app_topology_planning"),
        aliases=(
            "app matrix",
            "app matrix mode",
            "app_matrix",
            "app-matrix",
            "tenant agent",
            "应用矩阵",
            "应用矩阵模式",
            "矩阵管家",
        ),
    ),
    RootModeDescriptor(
        name="admin",
        label="全局管理员智能体",
        summary="面向系统运维的上帝视角，拥有所有高危操作权限，可跨应用矩阵游走和调度。",
        autonomy_level="durable_autonomy",
        primary_responsibilities=(
            "创建、删除和配置应用 (Apps)",
            "安装和管理全局技能 (Skills)",
            "跨矩阵数据迁移与系统级监控",
        ),
        typical_outputs=("system configurations", "new apps", "global policies"),
        enabled_capabilities=("interactive_chat", "durable_goal_loop"),
        aliases=(
            "admin",
            "system admin",
            "admin agent",
            "管理员",
            "全局管理员",
            "全局管理员模式",
            "系统智能体",
        ),
    ),
)

_PRODUCT_CONCEPTS: tuple[ProductConceptDescriptor, ...] = (
    ProductConceptDescriptor(
        name="tools",
        label="Tools",
        question="系统到底能执行哪些具体动作？",
        responsibility="承载可执行动作，如动态 Python 工具、模板工具、MCP 工具和执行循环。",
        capability_examples=("dynamic tool creation", "template tools", "MCP bridges", "execution loop"),
        typical_modules=("core/assets/tools/", "core/systems/execution/"),
    ),
    ProductConceptDescriptor(
        name="skills",
        label="Skills",
        question="经验和流程怎样复用？",
        responsibility="把可复用 know-how 打包成技能、提示词扩展、工具绑定和能力束。",
        capability_examples=("skill bundles", "prompt extensions", "tool bindings", "skill registry"),
        typical_modules=("core/assets/skills/",),
    ),
    ProductConceptDescriptor(
        name="agents",
        label="Agents",
        question="谁来承担任务与角色？",
        responsibility="承载 persona、记忆、工具访问、委派关系和治理边界。",
        capability_examples=("root agents", "subagents", "agent profiles", "delegation"),
        typical_modules=("core/assets/agents/",),
    ),
    ProductConceptDescriptor(
        name="workflows",
        label="Workflows",
        question="多步骤和多角色怎样协作？",
        responsibility="承载 DAG、审批、路由、暂停恢复、调度和多智能体协作节点。",
        capability_examples=("DAG", "approval nodes", "scheduling", "debate/consensus/supervisor"),
        typical_modules=("core/assets/workflows/",),
    ),
    ProductConceptDescriptor(
        name="apps",
        label="Apps",
        question="最终如何面向用户交付？",
        responsibility="承载 Web 控制台、工作区应用、交付入口和应用级协作表面。",
        capability_examples=("workspace apps", "web console", "API/CLI surfaces", "app brain topology"),
        typical_modules=("core/assets/apps/", "web/", "static/"),
    ),
)

_SUPPORTING_SYSTEMS: tuple[SupportingSystemDescriptor, ...] = (
    SupportingSystemDescriptor(
        name="runtime_foundation",
        label="运行时底座",
        purpose="给所有根模式和产品概念提供稳定的模型、路径、错误、事件和观测能力。",
        responsibilities=(
            "配置与路径解析",
            "模型解析与故障转移",
            "错误、重试、事件与可观测性",
        ),
        strengthens_concepts=("tools", "skills", "agents", "workflows", "apps"),
        typical_modules=(
            "core/systems/runtime/",
            "core/systems/middleware/",
            "core/systems/context/",
        ),
    ),
    SupportingSystemDescriptor(
        name="knowledge_and_memory",
        label="知识与记忆",
        purpose="提供文档摄入、检索、语义记忆和持久总结能力。",
        responsibilities=(
            "文档摄入与向量检索",
            "工作记忆与长期记忆",
            "压缩、摘要和检索增强",
        ),
        strengthens_concepts=("skills", "agents", "workflows", "apps"),
        typical_modules=(
            "core/systems/memory/",
            "core/systems/knowledge/",
        ),
    ),
    SupportingSystemDescriptor(
        name="governance_and_safety",
        label="治理与安全",
        purpose="让自治与创造能力保持可控、可审批、可恢复和可审计。",
        responsibilities=(
            "审批队列与风险分级",
            "沙箱与权限边界",
            "审计、护栏与策略继承",
        ),
        strengthens_concepts=("tools", "agents", "workflows", "apps"),
        typical_modules=("core/systems/governance/",),
    ),
    SupportingSystemDescriptor(
        name="delivery_and_integration",
        label="交付与集成",
        purpose="把能力对接到 Web/API/CLI、外部通道、PyHub 和 MCP 生态。",
        responsibilities=(
            "Web/API/CLI 入口",
            "外部通道与打包分发",
            "MCP 与生态集成",
        ),
        strengthens_concepts=("tools", "skills", "agents", "apps"),
        typical_modules=(
            "core/systems/integration/",
            "web/",
            "agent.py",
        ),
    ),
)

_INTERNAL_DOMAINS: tuple[InternalDomainDescriptor, ...] = (
    InternalDomainDescriptor(
        name="foundation",
        label="Foundation",
        purpose="配置、路径、错误、事件、模型与运行时底座。",
        examples=("core/systems/runtime/", "core/systems/middleware/", "core/systems/execution/", "core/systems/context/"),
    ),
    InternalDomainDescriptor(
        name="capabilities",
        label="Capabilities",
        purpose="工具、技能、知识和记忆等能力层。",
        examples=("core/assets/tools/", "core/assets/skills/", "core/systems/knowledge/", "core/systems/memory/"),
    ),
    InternalDomainDescriptor(
        name="agents",
        label="Agents",
        purpose="根智能体、子智能体、能力画像和委派运行时。",
        examples=("core/assets/agents/",),
    ),
    InternalDomainDescriptor(
        name="orchestration",
        label="Orchestration",
        purpose="工作流、调度、暂停恢复与跨角色协作。",
        examples=("core/assets/workflows/",),
    ),
    InternalDomainDescriptor(
        name="governance",
        label="Governance",
        purpose="审批、护栏、沙箱策略和审计。",
        examples=("core/systems/governance/",),
    ),
    InternalDomainDescriptor(
        name="surfaces",
        label="Surfaces",
        purpose="应用、通道、评估和用户交付面。",
        examples=("core/assets/apps/", "web/", "static/", "core/systems/integration/", "core/systems/eval/"),
    ),
)

_PACKAGE_TARGETS: tuple[PackageTargetDescriptor, ...] = (
    PackageTargetDescriptor(
        name="modes",
        label="Modes",
        path="core/modes/",
        purpose="三种根模式的运行时入口与模式边界实现层。",
        migration_scope=("assistant runtime", "app brain runtime", "admin runtime", "mode factories"),
    ),
    PackageTargetDescriptor(
        name="assets_tools",
        label="Assets / Tools",
        path="core/assets/tools/",
        purpose="工具资产的存储、创建、执行、风险控制和模板能力。",
        migration_scope=("tool_*", "execution_*", "mcp_hub.py"),
    ),
    PackageTargetDescriptor(
        name="assets_skills",
        label="Assets / Skills",
        path="core/assets/skills/",
        purpose="技能资产的 registry、backend、storage、marketplace 和 prompt 绑定。",
        migration_scope=("skill_*",),
    ),
    PackageTargetDescriptor(
        name="assets_agents",
        label="Assets / Agents",
        path="core/assets/agents/",
        purpose="智能体资产的定义、存储、委派、画像、治理桥接和子智能体运行时。",
        migration_scope=("agent_*", "subagent_*", "society_of_mind.py"),
    ),
    PackageTargetDescriptor(
        name="assets_workflows",
        label="Assets / Workflows",
        path="core/assets/workflows/",
        purpose="工作流资产的定义、执行、协作节点、调度、暂停恢复与 registry。",
        migration_scope=("workflow_*", "pyflow_engine.py", "task_*"),
    ),
    PackageTargetDescriptor(
        name="assets_apps",
        label="Assets / Apps",
        path="core/assets/apps/",
        purpose="应用资产的定义、编排、打包、验证与 APP Brain 绑定。",
        migration_scope=("app_*",),
    ),
    PackageTargetDescriptor(
        name="systems_runtime",
        label="Systems / Runtime",
        path="core/systems/runtime/",
        purpose="配置、路径、模型、错误、重试、bootstrap 和 runtime plumbing。",
        migration_scope=("config.py", "project_paths.py", "model_*", "retry_policy.py", "pybot_bootstrap.py"),
    ),
    PackageTargetDescriptor(
        name="systems_memory",
        label="Systems / Memory",
        path="core/systems/memory/",
        purpose="知识、记忆、文档摄入、向量检索和长期压缩。",
        migration_scope=("knowledge_*", "memory_*", "semantic_memory.py", "document_pipeline.py", "vector_store.py"),
    ),
    PackageTargetDescriptor(
        name="systems_governance",
        label="Systems / Governance",
        path="core/systems/governance/",
        purpose="审批、护栏、审计、沙箱、策略和治理编排。",
        migration_scope=("approval_*", "guardrails.py", "docker_sandbox.py", "intervention.py"),
    ),
    PackageTargetDescriptor(
        name="systems_integration",
        label="Systems / Integration",
        path="core/systems/integration/",
        purpose="外部集成、通道、Hub、Marketplace 与 backend adapters。",
        migration_scope=("channel_*", "pyhub_client.py", "backend_protocol.py", "skill_marketplace.py"),
    ),
)

_NOT_PRODUCT_CONCEPTS: tuple[str, ...] = (
    "MCP",
    "RAG",
    "approvals",
    "capability bus",
    "middleware",
    "observability",
    "failover",
)

_ANTI_SPRAWL_QUESTIONS: tuple[str, ...] = (
    "这是新的产品概念，还是只是 tools/skills/agents/workflows/apps 背后的支撑系统？",
    "它能否被建模成 runtime、backend、profile、registry 或 orchestrator？",
    "README 需要教它吗，还是只应在 core 内部实现层出现？",
)

_ROOT_MODE_INDEX = {mode.name: mode for mode in _ROOT_MODES}
_ROOT_MODE_ALIAS_MAP = {
    _alias_key(alias or mode.name): mode.name for mode in _ROOT_MODES for alias in (mode.name, *mode.aliases)
}


def normalize_root_mode(root_mode: str | None) -> str:
    return _ROOT_MODE_ALIAS_MAP.get(_alias_key(root_mode or "assistant"), "assistant")


def get_root_mode_descriptor(root_mode: str | None) -> RootModeDescriptor:
    return _ROOT_MODE_INDEX[normalize_root_mode(root_mode)]


def get_root_mode_label(root_mode: str | None) -> str:
    return get_root_mode_descriptor(root_mode).label


def list_root_modes() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _ROOT_MODES]


def list_product_concepts() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _PRODUCT_CONCEPTS]


def list_supporting_systems() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _SUPPORTING_SYSTEMS]


def list_internal_domains() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _INTERNAL_DOMAINS]


def list_package_targets() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _PACKAGE_TARGETS]


def build_system_summary() -> dict[str, Any]:
    return {
        "root_modes": len(_ROOT_MODES),
        "product_concepts": len(_PRODUCT_CONCEPTS),
        "supporting_systems": len(_SUPPORTING_SYSTEMS),
        "internal_domains": len(_INTERNAL_DOMAINS),
        "package_targets": len(_PACKAGE_TARGETS),
        "root_mode_labels": [mode.label for mode in _ROOT_MODES],
        "product_concept_labels": [concept.label for concept in _PRODUCT_CONCEPTS],
        "supporting_system_labels": [system.label for system in _SUPPORTING_SYSTEMS],
        "message": "3 root modes above 5 product concepts, strengthened by supporting systems.",
    }


def build_product_concept_prompt_section() -> str:
    lines = [
        "## 系统边界",
        "PyBot 只有三种根模式、五个产品概念，以及少量横切支撑系统。",
        "不要再把支撑系统讲成新的平台层。",
        "",
        "### 五个产品概念",
    ]
    for concept in _PRODUCT_CONCEPTS:
        lines.append(f"- {concept.label}: {concept.responsibility}")
    lines.extend(
        [
            "",
            "### 横切支撑系统",
            "- 运行时底座：模型、路径、错误、事件、观测与重试",
            "- 知识与记忆：摄入、检索、总结、压缩与长期记忆",
            "- 治理与安全：审批、风险分级、沙箱、审计与护栏",
            "- 交付与集成：Web/API/CLI、MCP、Hub、外部通道",
        ]
    )
    return "\n".join(lines)


def build_root_mode_boundary_prompt(root_mode: str | None) -> str:
    mode = get_root_mode_descriptor(root_mode)
    lines = [
        "## 模式边界",
        f"你当前处于：{mode.label}。",
        mode.summary,
        "",
        "当前模式优先职责：",
    ]
    lines.extend(f"- {item}" for item in mode.primary_responsibilities)
    if mode.name == "assistant":
        lines.extend(
            [
                "",
                "注意：不要默认把自己当成 应用矩阵或长期自治执行体。",
                "只有在用户明确要跨 APP 编排或长期自治推进时，才升级到更强模式的工作方式。",
            ]
        )
    elif mode.name == "app_matrix":
        lines.extend(
            [
                "",
                "注意：你主要负责应用级协作编排，不要把所有问题都升级成长期自治任务。",
                "如果只是普通问答，应保持清晰的助手体验；如果涉及长期演化，再向全局管理员模式升级。",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "注意：你负责长期目标与能力创造，但仍需通过治理、审批和恢复机制保持可控。",
                "不要把自己退化成只做一轮问答的助手，也不要跳过审计与边界约束。",
            ]
        )
    return "\n".join(lines)


def build_system_model() -> dict[str, Any]:
    return {
        "north_star": (
            "PyBot should be explained as three root modes above five product concepts, "
            "strengthened by supporting systems rather than presented as a repo-shaped feature pile."
        ),
        "root_mode_progression": [mode.name for mode in _ROOT_MODES],
        "root_modes": list_root_modes(),
        "product_concepts": list_product_concepts(),
        "supporting_systems": list_supporting_systems(),
        "internal_domains": list_internal_domains(),
        "package_targets": list_package_targets(),
        "not_product_concepts": list(_NOT_PRODUCT_CONCEPTS),
        "anti_sprawl_questions": list(_ANTI_SPRAWL_QUESTIONS),
        "canonical_rules": [
            "根模式只有三种：assistant / app_matrix / admin。",
            "用户可见产品概念只有五种：tools / skills / agents / workflows / apps。",
            "MCP、RAG、approvals、middleware、observability 等属于横切支撑系统，不应再讲成额外平台层。",
            (
                "core 内部可以按 foundation / capabilities / agents / orchestration / governance / "
                "surfaces 分域，但这些不是新的产品层。"
            ),
        ],
        "summary": build_system_summary(),
        "mode_profiles_modular": True,
    }
