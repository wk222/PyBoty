from __future__ import annotations

import importlib
import sys


def test_core_package_lazy_loads_exports():
    sys.modules.pop("core", None)
    sys.modules.pop("core.assets.tools.tool_storage", None)

    core = importlib.import_module("core")
    assert "core.assets.tools.tool_storage" not in sys.modules

    tool_storage_cls = core.ToolStorage
    assert tool_storage_cls.__name__ == "ToolStorage"
    assert "core.assets.tools.tool_storage" in sys.modules


def test_core_facade_preserves_common_exports():
    core = importlib.import_module("core")

    assert core.ProjectPaths.__name__ == "ProjectPaths"
    assert callable(core.get_templates_by_category)
    assert core.AdminPlanner.__name__ == "AdminPlanner"
