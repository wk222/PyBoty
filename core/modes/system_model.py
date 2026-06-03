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

Physical layout (post 2026-04 reorg):

  Layer 0 — Runtime Foundation (the trunk)
    core/systems/runtime/       bootstrap + foundation: config, paths, errors, event bus,
                                version, retry, multi_tenant, protocol adapters
                                (a2a, structured_output, patch_tool_calls, etc.),
                                runtime orchestrator, capability bundle, env/CLI
    core/systems/session/       session spine (event ledger, compaction, kernel)
    core/systems/context/       workspace view, context engine, prompts assembly,
                                projected runtime view, context budget/hygiene,
                                instruction assembly, private state registry
    core/systems/llm/           model resolver, failover, router
    core/plugin_sdk/            third-party plugin SDK (decorators, hook contexts, file lock)

  Layer 1 — Core Systems (cross-cutting infrastructure)
    core/systems/governance/    approvals, guardrails, sandbox, agent control
    core/systems/memory/        MemoryEngine + MemoryPipeline three-stage distillation
    core/systems/knowledge/     RAG pipeline, vector store, embedding
    core/systems/middleware/    agent/tool middleware chain, reasoning frame
    core/systems/execution/     code execution sandbox, swarm scheduler
    core/systems/capability/           capability bus runtime + registry
    core/systems/observability/ cost tracker, diagnostics, tracing setup
    core/systems/eval/          evaluation framework
    core/systems/integration/   channels, MCP, external content, PyHub
    core/systems/tasks/         long-running task registry — uniform facade over
                                PersistentAgentRunner, MonitorTask, TaskScheduler

  Layer 2 — Asset Domains (user-creatable, serializable resources)
    core/assets/tools/          tool creation, runtime, storage, templates
    core/assets/skills/         skill loading, registry, HTTP backends
    core/assets/agents/         agent storage, capability/middleware profiles, role policies
    core/assets/workflows/      DAG execution, scheduling, pause/resume
    core/assets/apps/           app templates, packager (HTML/CSS/JS bundles)

  Layer 3 — Product Modes (orchestration runtimes + identities)
    core/systems/agents/        subagent registry, persistent runner, team orchestrator,
                                agent creator, agent services, governance helpers
                                (orchestration runtime; depends on assets/agents)
    core/systems/apps/          app manager, matrix runtime/planner, orchestration tools,
                                verifier, iterative builder, marketplace tools
                                (orchestration runtime; depends on assets/apps)
    core/modes/                 ModeProfile (assistant/app_matrix/admin),
                                ExecutionCanvas (focused/balanced/deep),
                                packs, lifecycle, system_model, factories

  Layer 4 (consumer) — surfaces, not part of the core layering
    agent.py / api_server.py    public PyBot factory + FastAPI entrypoint
    web/                        FastAPI routers
    tests/ scripts/             may import from any layer

The strict rule: every module under ``core/`` may only import from layers at
its own level or below. The dataclasses in this file are the machine-readable
source of truth for that rule and feed the architectural-layer guard test.

Two explicit exemptions are recognized by the guard:

* **Namespace facades** (``core/__init__.py``, ``core/assets/__init__.py``,
  ``core/systems/__init__.py``) — pure re-export entry points consumed by the
  outermost layer. They cross-cut by design.
* **Assembly entrypoints** (``_ASSEMBLY_ENTRYPOINTS``) — bootstrap / capability
  bundle / orchestration / protocol-adapter files that legitimately stitch
  multiple layers together. Physically they live inside one layer; semantically
  they sit *above* L3 as the "L4 / consumer" wiring layer.

Reverse imports guarded by ``if TYPE_CHECKING:`` are also skipped (they have
no runtime effect on the dependency graph).
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


@dataclass(frozen=True)
class InteractionSurfaceDescriptor:
    name: str
    label: str
    route: str
    summary: str
    primary_jobs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "route": self.route,
            "summary": self.summary,
            "primary_jobs": list(self.primary_jobs),
        }


@dataclass(frozen=True)
class EcosystemFamilyDescriptor:
    name: str
    label: str
    singular_name: str
    summary: str
    ecosystem_route: str
    manager_route: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "singular_name": self.singular_name,
            "summary": self.summary,
            "ecosystem_route": self.ecosystem_route,
            "manager_route": self.manager_route,
        }


@dataclass(frozen=True)
class ArchitecturalLayerDescriptor:
    """One layer in PyBot's four-level dependency tree.

    The hierarchy enforces a strict rule: each layer may only import from
    layers at the same level or below, never upward.
    """

    name: str
    label: str
    level: int
    purpose: str
    packages: tuple[str, ...]
    public_api_module: str
    depends_on_layers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "level": self.level,
            "purpose": self.purpose,
            "packages": list(self.packages),
            "public_api_module": self.public_api_module,
            "depends_on_layers": list(self.depends_on_layers),
        }


_ARCHITECTURAL_LAYERS: tuple[ArchitecturalLayerDescriptor, ...] = (
    ArchitecturalLayerDescriptor(
        name="root",
        label="Layer 0 — 基础层 (Runtime Foundation)",
        level=0,
        purpose=(
            "启动与基础设施：bootstrap、事件总线、路径与错误、orchestrator、capability bundle、"
            "协议适配（a2a、structured_output、patch_tool_calls 等）、环境与 CLI；"
            "Session Spine（事件账本、压缩边界、kernel）；"
            "Workspace View、Context Engine、Prompts 组装、Projected Runtime View、"
            "Context Budget/Hygiene、Instruction Assembly、Private State 注册表；"
            "LLM 模型解析、failover 与 router；"
            "第三方插件 SDK（decorators、hook contexts、file lock）。"
            "这是所有能力生长的树干（THE TRUNK）。"
        ),
        packages=(
            "core/systems/runtime/",
            "core/systems/session/",
            "core/systems/context/",
            "core/systems/llm/",
            "core/plugin_sdk/",
        ),
        public_api_module="core.systems.runtime",
        depends_on_layers=(),
    ),
    ArchitecturalLayerDescriptor(
        name="core_systems",
        label="Layer 1 — 核心系统层 (Core Systems)",
        level=1,
        purpose=(
            "强化五个产品概念的横切基础设施："
            "治理与安全（AgentControlPolicy、审批、沙箱）；"
            "记忆与知识（MemoryEngine + MemoryPipeline 三阶段蒸馏流水线、RAG）；"
            "CapabilityBus + Registry；中间件链与推理框架；"
            "代码执行沙箱、可观测（成本/诊断/tracing）、评估、外部集成。"
        ),
        packages=(
            "core/systems/governance/",
            "core/systems/memory/",
            "core/systems/knowledge/",
            "core/systems/capability/",
            "core/systems/middleware/",
            "core/systems/execution/",
            "core/systems/observability/",
            "core/systems/eval/",
            "core/systems/integration/",
            "core/systems/tasks/",
        ),
        public_api_module="core.systems",
        depends_on_layers=("root",),
    ),
    ArchitecturalLayerDescriptor(
        name="asset_domains",
        label="Layer 2 — 领域对象层 (Asset Domains)",
        level=2,
        purpose=(
            "用户可创建/编辑/复用的资产：工具（创建/运行时/模板/风险）、"
            "技能（注册表/市场/HTTP 后端）、智能体（capability/middleware/role policy/storage）、"
            "工作流（PyFlow DAG/调度/暂停恢复）、应用（HTML/CSS/JS 模板/打包）。"
            "这些是构造 L3 编排实体的可序列化积木。"
        ),
        packages=(
            "core/assets/tools/",
            "core/assets/skills/",
            "core/assets/agents/",
            "core/assets/workflows/",
            "core/assets/apps/",
        ),
        public_api_module="core.assets",
        depends_on_layers=("root", "core_systems"),
    ),
    ArchitecturalLayerDescriptor(
        name="product_modes",
        label="Layer 3 — 身份层 (Product Modes)",
        level=3,
        purpose=(
            "自治实体与编排运行时：subagent 注册/runner/orchestrator、"
            "AppManager + 应用矩阵 runtime/planner/orchestration tools、"
            "ModeProfile（assistant/app_matrix/admin 身份）、"
            "ExecutionCanvas（focused/balanced/deep 资源策略）、"
            "AdminWatcher 守护进程与 mode pack。"
            "此层组装 L0/L1/L2 形成面向用户的产品身份。"
            "注意：core/systems/agents/ 与 core/systems/apps/ 物理位置在 systems/ 下，"
            "但语义归 L3（编排运行时），其纯数据/定义部分已下沉到 core/assets/agents/、"
            "core/assets/apps/。"
        ),
        packages=(
            "core/systems/agents/",
            "core/systems/apps/",
            "core/modes/",
        ),
        public_api_module="core.modes",
        depends_on_layers=("root", "core_systems", "asset_domains"),
    ),
)


# --------------------------------------------------------------------------- #
# Architectural-guard exemptions
# --------------------------------------------------------------------------- #
# Namespace facades: pure re-export entry points consumed by the outermost
# (consumer / L4) layer. They cross-cut all inner layers by design.
_NAMESPACE_FACADES: frozenset[str] = frozenset(
    {
        "core/__init__.py",
        "core/assets/__init__.py",
        "core/systems/__init__.py",
    }
)

# Assembly entrypoints: bootstrap / capability bundle / orchestration / protocol
# adapters that legitimately stitch multiple layers together. Physically they
# live inside one layer; semantically they sit *above* L3 as the L4 consumer
# wiring layer. Reverse imports from these files are excluded from the guard.
_ASSEMBLY_ENTRYPOINTS: frozenset[str] = frozenset(
    {
        # Runtime bootstrap & top-level wiring (compose every layer)
        "core/systems/runtime/pybot_bootstrap.py",
        "core/systems/runtime/runtime_capability_bundle.py",
        "core/systems/runtime/runtime_orchestrator.py",
        # Top-level context projections (assemble runtime + session + memory + agents)
        "core/systems/context/projected_runtime_view.py",
        "core/systems/context/prompts.py",
        # Session top-level engines (compose modes + memory + context)
        "core/systems/session/session_engine.py",
        "core/systems/session/session_memory_policy.py",
        # Middleware factory (assembles middleware stack across all layers)
        "core/systems/middleware/agent_middleware_factory.py",
        # Protocol/integration adapters (translate external protocols → modes)
        "core/systems/integration/openresponses.py",
        # Mode factories (compose multi-layer assets into mode-bound runtimes)
        "core/modes/factories.py",
    }
)


def list_namespace_facades() -> list[str]:
    """Return the ``__init__.py`` files that act as cross-layer facades."""

    return sorted(_NAMESPACE_FACADES)


def list_assembly_entrypoints() -> list[str]:
    """Return the bootstrap/orchestration files exempted from the guard."""

    return sorted(_ASSEMBLY_ENTRYPOINTS)


def is_guard_exempt(relative_path: str) -> bool:
    """True if ``relative_path`` (forward-slashed, relative to repo root) is exempt."""

    normalized = relative_path.replace("\\", "/")
    return normalized in _NAMESPACE_FACADES or normalized in _ASSEMBLY_ENTRYPOINTS


def list_architectural_layers() -> list[dict[str, Any]]:
    return [layer.to_dict() for layer in _ARCHITECTURAL_LAYERS]


def get_architectural_layer(name: str) -> ArchitecturalLayerDescriptor | None:
    for layer in _ARCHITECTURAL_LAYERS:
        if layer.name == name:
            return layer
    return None


def build_architectural_tree() -> dict[str, Any]:
    """Return the four-layer dependency tree as a structured dict."""
    return {
        "rule": (
            "Each layer may only import from layers at the same level or below. "
            "Never introduce upward dependencies."
        ),
        "layers": list_architectural_layers(),
        "layer_count": len(_ARCHITECTURAL_LAYERS),
        "dependency_direction": "基础层(L0) → 核心系统层(L1) → 领域对象层(L2) → 身份层(L3)",
        "tree_metaphor": (
            "Layer 0 基础层（runtime, session, context, llm, plugin_sdk）是树干。"
            "Layer 1 核心系统层（governance, memory, knowledge, capability, middleware, "
            "execution, observability, eval, integration）是一级枝干。"
            "Layer 2 领域对象层（tools, skills, agents, workflows, apps）是二级枝干。"
            "Layer 3 身份层（systems/agents 编排、systems/apps 矩阵运行时、modes/"
            "profile/canvas/packs/admin）是树冠。"
        ),
        "exemptions": {
            "namespace_facades": list_namespace_facades(),
            "assembly_entrypoints": list_assembly_entrypoints(),
            "type_checking_imports": "Imports inside ``if TYPE_CHECKING:`` blocks are skipped.",
        },
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
        typical_modules=("core/assets/agents/", "core/systems/agents/"),
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
        typical_modules=("core/systems/apps/", "web/", "static/"),
    ),
)

_INTERACTION_SURFACES: tuple[InteractionSurfaceDescriptor, ...] = (
    InteractionSurfaceDescriptor(
        name="chat",
        label="Chat",
        route="/chat",
        summary="主工作会话。默认从这里开始提问、执行、切换模式档位和创建能力。",
        primary_jobs=("开始工作", "运行当前任务", "在会话里切换模式/角色"),
    ),
    InteractionSurfaceDescriptor(
        name="governance",
        label="Governance",
        route="/governance",
        summary="风险、审批和网关控制面。让自治能力保持可见、可管、可恢复。",
        primary_jobs=("审批高风险动作", "查看治理策略", "管理网关与路由"),
    ),
    InteractionSurfaceDescriptor(
        name="ecosystem",
        label="Ecosystem",
        route="/ecosystem",
        summary="统一的可复用资产工作台。Apps、Workflows、Skills、Tools、Agents 在这里统一浏览。",
        primary_jobs=("浏览可复用资产", "进入高级管理器", "发现市场与系统结构"),
    ),
)

_ECOSYSTEM_FAMILIES: tuple[EcosystemFamilyDescriptor, ...] = (
    EcosystemFamilyDescriptor(
        name="apps",
        label="Apps",
        singular_name="app",
        summary="面向用户交付的应用入口与运行外壳。",
        ecosystem_route="/ecosystem?asset=apps",
        manager_route="/apps",
    ),
    EcosystemFamilyDescriptor(
        name="workflows",
        label="Workflows",
        singular_name="workflow",
        summary="多步骤编排、调度与暂停恢复的执行蓝图。",
        ecosystem_route="/ecosystem?asset=workflows",
        manager_route="/workflows",
    ),
    EcosystemFamilyDescriptor(
        name="skills",
        label="Skills",
        singular_name="skill",
        summary="可复用经验、提示扩展和工具绑定的能力包。",
        ecosystem_route="/ecosystem?asset=skills",
        manager_route="/skills",
    ),
    EcosystemFamilyDescriptor(
        name="tools",
        label="Tools",
        singular_name="tool",
        summary="具体可执行动作的统一动作库。",
        ecosystem_route="/ecosystem?asset=tools",
        manager_route="/tools",
    ),
    EcosystemFamilyDescriptor(
        name="agents",
        label="Agents",
        singular_name="agent",
        summary="承担角色、记忆和委派关系的执行者。",
        ecosystem_route="/ecosystem?asset=agents",
        manager_route="/agents",
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
        examples=("core/assets/agents/", "core/systems/agents/"),
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
        examples=("core/systems/apps/", "core/assets/apps/", "web/", "static/", "core/systems/integration/", "core/systems/eval/"),
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
        purpose="智能体资产的定义、存储、委派画像与 role policy（运行时编排见 core/systems/agents/）。",
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
        path="core/systems/apps/",
        purpose="应用编排运行时、矩阵规划、验证与 APP Brain 绑定（资产模板见 core/assets/apps/）。",
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


def list_interaction_surfaces() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _INTERACTION_SURFACES]


def list_ecosystem_families() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _ECOSYSTEM_FAMILIES]


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
        "interaction_surfaces": len(_INTERACTION_SURFACES),
        "ecosystem_families": len(_ECOSYSTEM_FAMILIES),
        "supporting_systems": len(_SUPPORTING_SYSTEMS),
        "internal_domains": len(_INTERNAL_DOMAINS),
        "package_targets": len(_PACKAGE_TARGETS),
        "root_mode_labels": [mode.label for mode in _ROOT_MODES],
        "product_concept_labels": [concept.label for concept in _PRODUCT_CONCEPTS],
        "interaction_surface_labels": [surface.label for surface in _INTERACTION_SURFACES],
        "ecosystem_family_labels": [family.label for family in _ECOSYSTEM_FAMILIES],
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
        "interaction_surfaces": list_interaction_surfaces(),
        "ecosystem_families": list_ecosystem_families(),
        "root_modes": list_root_modes(),
        "product_concepts": list_product_concepts(),
        "supporting_systems": list_supporting_systems(),
        "internal_domains": list_internal_domains(),
        "package_targets": list_package_targets(),
        "architectural_tree": build_architectural_tree(),
        "not_product_concepts": list(_NOT_PRODUCT_CONCEPTS),
        "anti_sprawl_questions": list(_ANTI_SPRAWL_QUESTIONS),
        "canonical_rules": [
            "根模式只有三种：assistant / app_matrix / admin。",
            "一级交互入口默认只有三个：chat / governance / ecosystem。",
            "用户可见产品概念只有五种：tools / skills / agents / workflows / apps。",
            "MCP、RAG、approvals、middleware、observability 等属于横切支撑系统，不应再讲成额外平台层。",
            (
                "代码按四层树形组织：基础层 L0 (runtime/session/context) → "
                "核心系统层 L1 (governance/memory/capability) → "
                "领域对象层 L2 (tools/skills/agents/workflows) → "
                "身份层 L3 (apps/modes/canvas)。每层只依赖同层或更低层，绝不反向。"
            ),
        ],
        "summary": build_system_summary(),
        "mode_profiles_modular": True,
    }
