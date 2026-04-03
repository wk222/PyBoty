"""Shared typed-memory taxonomy across session and durable memory systems.

Memory is organized along two orthogonal axes:

  TYPE  — what kind of information this is (user pref, feedback, project record, etc.)
  LAYER — which lifecycle scope owns this memory (workspace, session, agent, admin)

Layer descriptors
-----------------
  workspace  — file views, project structure, workspace rules, read-state cache.
               Populated by file operations and workspace scans. Compacted first
               when context pressure is high; regenerated cheaply on demand.

  session    — working notes, tool transcripts, interaction snapshots, per-turn
               compaction results. Scoped to the current chat/run session.
               Compacted to session notebook on session close.

  agent      — role-specific preferences, agent decision history, capability
               configurations. Persists across sessions for the same agent identity.
               Compacted on agent lifecycle events.

  admin      — long-term goal state, executive decisions, cross-session project
               records, durable loop state. Owned by the admin runtime. Persists
               indefinitely; compacted by the admin memory manager.

Type → default layer matrix
---------------------------
  session_note  →  session   (ephemeral, never durable)
  user          →  agent     (durable preferences tied to agent role)
  feedback      →  agent     (durable lessons learned)
  project       →  admin     (durable project records with dates)
  reference     →  session   (durable facts, default to session unless long-lived)
"""

from __future__ import annotations

from dataclasses import dataclass

SESSION_MEMORY_TYPE = "session_note"

_TYPE_TO_SECTION = {
    "user": "用户偏好",
    "feedback": "学到的经验",
    "project": "已完成的项目",
    "reference": "重要事实",
}

_TYPE_TO_CATEGORY = {
    "user": "preference",
    "feedback": "decision",
    "project": "entity",
    "reference": "fact",
}

_SECTION_ALIASES = {
    "preferences": "user",
    "用户偏好": "user",
    "facts": "reference",
    "重要事实": "reference",
    "decisions": "feedback",
    "学到的经验": "feedback",
    "projects": "project",
    "已完成的项目": "project",
    "reference": "reference",
}

_DURABLE_TYPES: frozenset[str] = frozenset({"user", "feedback", "project", "reference"})
ALL_MEMORY_TYPES: frozenset[str] = frozenset({SESSION_MEMORY_TYPE}) | _DURABLE_TYPES

_TYPE_DEFAULT_LAYER: dict[str, str] = {
    SESSION_MEMORY_TYPE: "session",
    "user": "agent",
    "feedback": "agent",
    "project": "admin",
    "reference": "session",
}

_LAYER_ALLOWED_TYPES: dict[str, frozenset[str]] = {
    "workspace": frozenset({SESSION_MEMORY_TYPE, "reference"}),
    "session": ALL_MEMORY_TYPES,
    "agent": frozenset({"user", "feedback", SESSION_MEMORY_TYPE}),
    "admin": frozenset({"project", "reference", "feedback", SESSION_MEMORY_TYPE}),
}

ALL_LAYERS: frozenset[str] = frozenset(_LAYER_ALLOWED_TYPES)


@dataclass(frozen=True)
class MemoryLayerDescriptor:
    name: str
    label: str
    description: str
    compaction_priority: int
    persists_across_sessions: bool
    default_types: frozenset[str]


LAYER_DESCRIPTORS: dict[str, MemoryLayerDescriptor] = {
    "workspace": MemoryLayerDescriptor(
        name="workspace",
        label="工作区上下文",
        description="文件视图、项目结构、工作区规则和读状态缓存。上下文压力最高时优先压缩；可按需重新生成。",
        compaction_priority=1,
        persists_across_sessions=False,
        default_types=frozenset({"reference"}),
    ),
    "session": MemoryLayerDescriptor(
        name="session",
        label="会话工作记忆",
        description="工作笔记、工具记录、交互快照和每轮压缩结果。会话结束时压缩为会话笔记本。",
        compaction_priority=2,
        persists_across_sessions=False,
        default_types=frozenset({SESSION_MEMORY_TYPE, "reference"}),
    ),
    "agent": MemoryLayerDescriptor(
        name="agent",
        label="智能体角色记忆",
        description="角色偏好、智能体决策历史和能力配置。跨会话持久化，与智能体身份绑定。",
        compaction_priority=3,
        persists_across_sessions=True,
        default_types=frozenset({"user", "feedback"}),
    ),
    "admin": MemoryLayerDescriptor(
        name="admin",
        label="管理员持久记忆",
        description="长期目标状态、执行决策、跨会话项目记录和持久循环状态。由管理员运行时管理。",
        compaction_priority=4,
        persists_across_sessions=True,
        default_types=frozenset({"project", "reference", "feedback"}),
    ),
}


def normalize_memory_type(memory_type: str = "") -> str:
    normalized = str(memory_type).strip().lower()
    return normalized if normalized in _DURABLE_TYPES else SESSION_MEMORY_TYPE


def default_layer_for_type(memory_type: str) -> str:
    normalized = normalize_memory_type(memory_type)
    return _TYPE_DEFAULT_LAYER.get(normalized, "session")


def validate_layer_for_type(memory_type: str, layer: str) -> str | None:
    """Return an error message if the layer is incompatible with the memory type, else None."""
    normalized_type = normalize_memory_type(memory_type)
    normalized_layer = str(layer).strip().lower() or "session"
    if normalized_layer not in ALL_LAYERS:
        return f"unknown memory layer: {normalized_layer!r}; valid layers: {sorted(ALL_LAYERS)}"
    allowed = _LAYER_ALLOWED_TYPES[normalized_layer]
    if normalized_type not in allowed:
        return (
            f"memory type {normalized_type!r} is not allowed in layer {normalized_layer!r}; "
            f"allowed types: {sorted(allowed)}"
        )
    return None


def section_for_memory_type(memory_type: str) -> str:
    normalized = normalize_memory_type(memory_type)
    if normalized == SESSION_MEMORY_TYPE:
        return "会话笔记"
    return _TYPE_TO_SECTION[normalized]


def category_for_memory_type(memory_type: str) -> str:
    normalized = normalize_memory_type(memory_type)
    if normalized == SESSION_MEMORY_TYPE:
        return "other"
    return _TYPE_TO_CATEGORY[normalized]


def memory_type_for_section(section: str) -> str:
    normalized = str(section).strip()
    if not normalized:
        return SESSION_MEMORY_TYPE
    return _SECTION_ALIASES.get(normalized, _SECTION_ALIASES.get(normalized.lower(), SESSION_MEMORY_TYPE))
