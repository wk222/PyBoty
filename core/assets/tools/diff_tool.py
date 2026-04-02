"""Diff viewing and generation tool for agents.

Allows agents to:
  - Compare two text blocks (before/after) and produce a unified diff.
  - Apply a unified patch to a base text.
  - Summarise what changed in human-readable terms.

The tool operates purely in-memory and does not require external services.
"""

from __future__ import annotations

import difflib
import json
import logging
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_INPUT_SIZE = 512 * 1024  # 512 KB per input


class ViewDiffInput(BaseModel):
    before: str = Field(description="Original text (before changes)")
    after: str = Field(description="Modified text (after changes)")
    context_lines: int = Field(default=3, description="Number of context lines around each change")
    filename: str = Field(default="file", description="Filename label for the diff header")


class ApplyPatchInput(BaseModel):
    base_text: str = Field(description="Original text to apply the patch to")
    patch: str = Field(description="Unified diff patch string")


class DiffSummaryInput(BaseModel):
    before: str = Field(description="Original text")
    after: str = Field(description="Modified text")


def generate_unified_diff(
    before: str,
    after: str,
    *,
    filename: str = "file",
    context_lines: int = 3,
) -> str:
    """Generate a unified diff string from two text blocks."""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=context_lines,
    )
    return "".join(diff)


def diff_stats(before: str, after: str) -> dict[str, int]:
    """Compute insertion/deletion/modification statistics."""
    before_lines = before.splitlines()
    after_lines = after.splitlines()

    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    insertions = 0
    deletions = 0
    modifications = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            insertions += j2 - j1
        elif tag == "delete":
            deletions += i2 - i1
        elif tag == "replace":
            modifications += max(i2 - i1, j2 - j1)

    return {
        "insertions": insertions,
        "deletions": deletions,
        "modifications": modifications,
        "before_lines": len(before_lines),
        "after_lines": len(after_lines),
    }


def apply_patch(base_text: str, patch: str) -> str | None:
    """Apply a unified diff patch to base_text. Returns None on failure."""
    base_lines = base_text.splitlines(keepends=True)
    result_lines = list(base_lines)
    offset = 0

    current_hunk_start = -1
    hunk_removes: list[tuple[int, str]] = []
    hunk_adds: list[str] = []
    in_hunk = False

    for line in patch.splitlines(keepends=True):
        if line.startswith("@@"):
            if in_hunk:
                result_lines, offset = _apply_hunk(result_lines, hunk_removes, hunk_adds, current_hunk_start, offset)

            parts = line.split()
            if len(parts) >= 2:
                old_range = parts[1]
                start = int(old_range.split(",")[0].lstrip("-"))
                current_hunk_start = start - 1
            hunk_removes = []
            hunk_adds = []
            in_hunk = True
        elif in_hunk:
            if line.startswith("-"):
                hunk_removes.append((len(hunk_removes), line[1:]))
            elif line.startswith("+"):
                hunk_adds.append(line[1:])

    if in_hunk:
        result_lines, offset = _apply_hunk(result_lines, hunk_removes, hunk_adds, current_hunk_start, offset)

    return "".join(result_lines)


def _apply_hunk(
    lines: list[str],
    removes: list[tuple[int, str]],
    adds: list[str],
    start: int,
    offset: int,
) -> tuple[list[str], int]:
    pos = start + offset
    for _ in removes:
        if 0 <= pos < len(lines):
            lines.pop(pos)
    for i, add_line in enumerate(adds):
        lines.insert(pos + i, add_line)
    new_offset = offset - len(removes) + len(adds)
    return lines, new_offset


class ViewDiffTool(BaseTool):
    """Generate a unified diff between two text blocks."""

    name: str = "view_diff"
    description: str = (
        "Compare two text blocks (before and after) and produce a unified diff. "
        "Useful for reviewing code changes, configuration updates, or any text modifications. "
        "Returns the diff along with statistics (insertions, deletions, modifications)."
    )
    args_schema: type[BaseModel] = ViewDiffInput

    def _run(
        self,
        before: str,
        after: str,
        context_lines: int = 3,
        filename: str = "file",
    ) -> str:
        if len(before) > _MAX_INPUT_SIZE or len(after) > _MAX_INPUT_SIZE:
            return json.dumps(
                {"success": False, "error": f"Input exceeds {_MAX_INPUT_SIZE} bytes limit"},
                ensure_ascii=False,
            )

        diff = generate_unified_diff(before, after, filename=filename, context_lines=context_lines)
        stats = diff_stats(before, after)

        if not diff.strip():
            return json.dumps({"success": True, "diff": "", "stats": stats, "identical": True}, ensure_ascii=False)

        return json.dumps(
            {"success": True, "diff": diff, "stats": stats, "identical": False},
            ensure_ascii=False,
        )


class ApplyPatchTool(BaseTool):
    """Apply a unified diff patch to a base text."""

    name: str = "apply_patch"
    description: str = (
        "Apply a unified diff (patch) to a base text and return the result. "
        "Use when you have a diff and want to produce the modified output."
    )
    args_schema: type[BaseModel] = ApplyPatchInput

    def _run(self, base_text: str, patch: str) -> str:
        try:
            result = apply_patch(base_text, patch)
            if result is None:
                return json.dumps({"success": False, "error": "Failed to apply patch"}, ensure_ascii=False)
            return json.dumps({"success": True, "result": result}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


class DiffSummaryTool(BaseTool):
    """Summarise the differences between two text blocks."""

    name: str = "diff_summary"
    description: str = (
        "Produce a human-readable summary of what changed between two text blocks, "
        "including line-level statistics and a list of changed regions."
    )
    args_schema: type[BaseModel] = DiffSummaryInput

    def _run(self, before: str, after: str) -> str:
        stats = diff_stats(before, after)
        before_lines = before.splitlines()
        after_lines = after.splitlines()

        matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
        regions: list[dict[str, Any]] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            regions.append({
                "type": tag,
                "before_range": f"L{i1 + 1}-L{i2}",
                "after_range": f"L{j1 + 1}-L{j2}",
                "preview": (before_lines[i1] if i1 < len(before_lines) else "")[:80],
            })

        return json.dumps(
            {"success": True, "stats": stats, "changed_regions": regions[:50]},
            ensure_ascii=False,
        )


def get_diff_tools() -> list[BaseTool]:
    """Return all diff-related tools."""
    return [ViewDiffTool(), ApplyPatchTool(), DiffSummaryTool()]
