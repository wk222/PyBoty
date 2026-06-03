"""Persisted app asset definitions.

This subpackage holds the **declarative, serializable** parts of PyBot apps
(HTML/CSS/JS templates, app bundle packaging). It is a Layer 2 (asset domain)
sibling of ``core.assets.tools`` / ``core.assets.skills`` /
``core.assets.workflows`` / ``core.assets.agents``.

The runtime orchestration counterparts (app manager, matrix runtime/planner,
verifier, iterative builder, etc.) live under ``core.systems.apps`` and
belong to Layer 3 (product modes).
"""

from __future__ import annotations

from core.assets.apps.packager import AppPackager
from core.assets.apps.templates import (
    APP_TEMPLATES,
    CHAT_APP_CSS,
    CHAT_APP_JS,
    RAG_APP_CSS,
    RAG_APP_JS,
    WORKFLOW_APP_CSS,
    WORKFLOW_APP_JS,
    build_chat_html,
    build_rag_html,
    build_workflow_html,
)

__all__ = [
    "APP_TEMPLATES",
    "AppPackager",
    "CHAT_APP_CSS",
    "CHAT_APP_JS",
    "RAG_APP_CSS",
    "RAG_APP_JS",
    "WORKFLOW_APP_CSS",
    "WORKFLOW_APP_JS",
    "build_chat_html",
    "build_rag_html",
    "build_workflow_html",
]
