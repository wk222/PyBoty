"""Shared typed-memory taxonomy across session and durable memory systems."""

from __future__ import annotations

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


def normalize_memory_type(memory_type: str = "") -> str:
    normalized = str(memory_type).strip().lower()
    return normalized if normalized in {"user", "feedback", "project", "reference"} else SESSION_MEMORY_TYPE


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
