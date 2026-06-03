6-Trunk 树形架构加固

核心理念

当前 plan.md 把 6 个 Trunk 画成并列的，但实际依赖关系是一棵树：

flowchart TD
    subgraph L0 ["Layer 0: 基础层 (Runtime Foundation)"]
        runtime["runtime/\nconfig, paths, errors, events, bootstrap"]
        session["runtime/session/\nSession Spine"]
        context["context/\nWorkspace View + Context Engine"]
    end

    subgraph L1 ["Layer 1: 核心系统层 (Core Systems)"]
        governance["governance/\napprovals, guardrails, AgentControlPolicy, sandbox"]
        memory["memory/ + knowledge/\nGarden, Vector, Semantic, MemoryDistill"]
        bus["bus/\nCapabilityBus + Registry"]
        middleware["middleware/\nmiddleware chain, reasoning"]
    end

    subgraph L2 ["Layer 2: 领域对象层 (Asset Domains)"]
        tools["assets/tools/\ntool runtime, creation, templates"]
        skills["assets/skills/\nskill registry, marketplace"]
        agents["assets/agents/\nsubagent registry, delegation"]
        workflows["assets/workflows/\nDAG engine, scheduling"]
    end

    subgraph L3 ["Layer 3: 身份层 (Product Modes)"]
        apps["assets/apps/\nApp Matrix, Brain, Orchestration"]
        modes["modes/\nassistant, app_matrix, admin + ExecutionCanvas"]
    end

    runtime --> session
    runtime --> context
    session --> governance
    session --> memory
    session --> bus
    session --> middleware
    context --> governance
    context --> memory
    governance --> tools
    governance --> agents
    memory --> tools
    memory --> skills
    memory --> agents
    bus --> tools
    bus --> skills
    bus --> agents
    middleware --> tools
    tools --> workflows
    agents --> workflows
    skills --> apps
    workflows --> apps
    tools --> apps
    agents --> apps
    apps --> modes

规则：每一层只依赖同层或更低层，绝不反向。



6 项具体工作

1. 在 system_model.py 中新增 ArchitecturalLayer 描述符

文件: [core/modes/system_model.py](core/modes/system_model.py)

在现有 RootModeDescriptor、ProductConceptDescriptor 等之后新增：

@dataclass(frozen=True)
class ArchitecturalLayerDescriptor:
    name: str
    label: str
    level: int
    purpose: str
    packages: tuple[str, ...]
    public_api_module: str
    depends_on_layers: tuple[str, ...]

定义四层：





root (level=0): runtime, session, context



core_systems (level=1): governance, memory, knowledge, bus, middleware



asset_domains (level=2): tools, skills, agents, workflows



product_modes (level=3): apps, modes

新增 _ARCHITECTURAL_LAYERS 常量元组和 build_architectural_tree() 函数（类似现有 build_system_model()）。

2. 加固 Session Spine init.py（树根）

文件: [core/systems/runtime/session/__init__.py](core/systems/runtime/session/__init__.py)

当前问题：





文档写 "internal modules"



导出 6 个 _ 前缀的内部函数



缺少 SessionRuntime, SessionKernel, SessionEvent 等核心公共 API

改为：





文档改为 "Session spine: the canonical run backbone for all PyBot capabilities"



移除所有 _ 前缀导出



新增导出: SessionRuntime, SessionKernel, SessionSidechain, SessionEvent, SessionEventQueue, CompactionBoundary, SessionMemoryDecision, PyBotSessionEngine, RunResult

3. 加固 Context/Workspace View init.py（树根）

文件: [core/systems/context/__init__.py](core/systems/context/__init__.py)

当前问题：只导出 5 个符号，缺少 ContextEngine, DefaultContextEngine, ContextStrategy 等。

新增导出: ContextEngine, DefaultContextEngine, ContextStrategy, BufferedChatContext, TokenLimitedChatContext, build_context_strategy

4. 加固 Memory init.py（一级枝）

文件: [core/systems/memory/__init__.py](core/systems/memory/__init__.py)

当前问题：只导出基础的 MemoryManager 和 SemanticMemoryManager，缺少 Garden、admin memory、memory tools 等。

新增导出: MarkdownGarden, GardenNote, get_garden_tools, AdminMemory, get_memory_tools, MemoryTaxonomy, SessionMemoryExtractor（按需从子模块懒加载）

5. 加固 Tools init.py（二级枝）

文件: [core/assets/tools/__init__.py](core/assets/tools/__init__.py)

当前问题：





使用原始 __getattr__ 模式，没有 _EXPORTS 表也没有 __all__



缺少 tool_risk, file_system_tools, tool_call_runtime, session_notes 等常用子模块的导出

改为 _EXPORTS + __getattr__ + __all__ + __dir__ 的标准模式（与 agents/__init__.py 一致），覆盖所有常被直引的子模块公共符号。

6. 更新 plan.md 和 ARCHITECTURE.md

文件: [plan.md](plan.md)

将 "Architecture Tree (The 6-Trunk Model)" 部分从扁平的 6 并列重写为 4 层树形结构，明确标注每层的依赖方向。

文件: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

更新 "Physical Layout" 部分，增加 "Dependency Layers" 小节，写明 4 层依赖规则。



不在本轮范围





重定向消费方的绕行 import（如 from core.systems.context.context_engine import ... 改为 from core.systems.context import ...）——数量多，留到下一轮系统性扫描



governance, bus, skills, workflows 的 init.py——已经比较干净，本轮先加固上述 4 个问题最大的



core/ 根目录 shim 文件删除——等消费方全部迁完再处理

