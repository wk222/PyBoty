from __future__ import annotations

from core.assets.tools.file_system_tools import get_file_system_tools
from core.systems.context.workspace_view import WorkspaceViewService


def _tool_by_name(tools, name: str):
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"Tool not found: {name}")


def test_workspace_view_deduplicates_full_and_exact_partial_ranges(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    workspace_view = WorkspaceViewService()
    tools = get_file_system_tools(
        allowed_root=str(tmp_path),
        workspace_view=workspace_view,
    )
    read_tool = _tool_by_name(tools, "read_file")

    assert read_tool._run("app.py") == "alpha\nbeta\ngamma\n"

    full_stub = read_tool._run("app.py")
    assert "[FILE_UNCHANGED]" in full_stub
    assert "视图: full" in full_stub

    partial_view = read_tool._run("app.py", offset=1, limit=1)
    assert partial_view.startswith("[partial view] 行 2-2/3 of app.py")
    assert partial_view.endswith("beta\n")

    partial_stub = read_tool._run("app.py", offset=1, limit=1)
    assert "[FILE_UNCHANGED]" in partial_stub
    assert "视图: partial 2-2/3" in partial_stub

    different_partial = read_tool._run("app.py", offset=0, limit=1)
    assert different_partial.startswith("[partial view] 行 1-1/3 of app.py")
    assert different_partial.endswith("alpha\n")

    full_stub_again = read_tool._run("app.py")
    assert "[FILE_UNCHANGED]" in full_stub_again
    assert "视图: full" in full_stub_again

    projection = workspace_view.build_projection(limit=8)
    assert projection["recent_paths"] == [str(target)]
    assert projection["recent_views"][-1]["path"] == str(target)
    assert projection["recent_views"][-1]["view_kind"] == "partial"
    assert projection["partial_views"] >= 1
    assert projection["path_labels"] == ["app.py"]

    assert workspace_view.stats["full_hits"] >= 2
    assert workspace_view.stats["partial_hits"] >= 1


def test_workspace_view_invalidates_on_write_and_replace(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("first\nsecond\n", encoding="utf-8")

    workspace_view = WorkspaceViewService()
    tools = get_file_system_tools(
        allowed_root=str(tmp_path),
        workspace_view=workspace_view,
    )
    read_tool = _tool_by_name(tools, "read_file")
    write_tool = _tool_by_name(tools, "write_file")
    replace_tool = _tool_by_name(tools, "str_replace")

    assert read_tool._run("app.py") == "first\nsecond\n"
    assert "[FILE_UNCHANGED]" in read_tool._run("app.py")

    assert "成功写入文件" in write_tool._run("app.py", "updated\nbody\n")
    assert read_tool._run("app.py") == "updated\nbody\n"

    assert read_tool._run("app.py", offset=0, limit=1).startswith("[partial view] 行 1-1/2 of app.py")
    assert "成功替换文件" in replace_tool._run("app.py", "updated", "fresh")
    assert read_tool._run("app.py") == "fresh\nbody\n"

    assert workspace_view.stats["invalidations"] >= 2
