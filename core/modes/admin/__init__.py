"""Admin profile — runtime, planner, pack, and watcher in one place.

The Admin profile gives PyBot self-orchestration powers: it can plan
multi-step ecosystem actions, persist intent across runs, watch global
telemetry, and surface capability gaps. All of those moving parts live in
this single subpackage so the responsibilities stay close together.

Submodules:

- ``planner``: turns high-level admin intents into structured ``AdminPlan``s.
- ``runtime``: the ``PersistentAdminRuntime`` that drives the plan step by step.
- ``pack``: the ``AdminPack`` that exposes admin APIs to the mode framework.
- ``watcher``: a background daemon that synthesises ecosystem health reports.
"""

from core.modes.admin.pack import AdminPack
from core.modes.admin.planner import (
    AdminPlan,
    AdminPlanner,
    fallback_admin_plan,
)
from core.modes.admin.runtime import PersistentAdminRuntime
from core.modes.admin.watcher import AdminWatcherDaemon

__all__ = [
    "AdminPack",
    "AdminPlan",
    "AdminPlanner",
    "AdminWatcherDaemon",
    "PersistentAdminRuntime",
    "fallback_admin_plan",
]
