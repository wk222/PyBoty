"""YAML configuration support for agents and tasks.

Loads agent definitions from agents.yaml and task definitions from
tasks.yaml. Supports {placeholder} interpolation at kickoff time.
Coexists with the existing JSON config — YAML adds, doesn't replace.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


def _require_yaml() -> None:
    if yaml is None:
        raise ImportError("PyYAML is required for YAML config. Install with: pip install pyyaml")


def interpolate_placeholders(text: str, variables: dict[str, Any]) -> str:
    """Replace {key} placeholders with values from variables dict.

    Only replaces keys that exist in variables. Unmatched placeholders
    are left as-is.
    """

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        return match.group(0)

    return re.sub(r"\{(\w+)\}", _replace, text)


def _interpolate_deep(obj: Any, variables: dict[str, Any]) -> Any:
    """Recursively interpolate placeholders in strings within nested structures."""
    if isinstance(obj, str):
        return interpolate_placeholders(obj, variables)
    if isinstance(obj, dict):
        return {k: _interpolate_deep(v, variables) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_deep(item, variables) for item in obj]
    return obj


def load_agents_yaml(
    path: str | Path,
    *,
    variables: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load agent definitions from a YAML file.

    Expected format:
        agent_name:
          role: ...
          description: ...
          system_prompt: |
            ...
          capabilities: [...]
          profile: researcher
          model: gpt-4
          temperature: 0.7

    Returns a list of dicts, each with at least 'name', 'role',
    'description', 'system_prompt'.
    """
    _require_yaml()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Agents YAML not found: {p}")

    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"agents.yaml must be a YAML mapping, got {type(raw).__name__}")

    agents: list[dict[str, Any]] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            logger.warning("Skipping agent %r: value is not a mapping", name)
            continue

        agent: dict[str, Any] = {"name": str(name)}
        agent["role"] = spec.get("role", name)
        agent["description"] = spec.get("description", "")
        agent["system_prompt"] = spec.get("system_prompt", "")
        agent["capabilities"] = spec.get("capabilities", [])
        agent["model"] = spec.get("model", "gemini-3-flash-preview")
        agent["temperature"] = float(spec.get("temperature", 0.7))

        if "profile" in spec:
            agent["capability_profile"] = {"preset": spec["profile"]}
        if "middleware" in spec:
            agent["middleware_profile"] = spec["middleware"]

        agents.append(agent)

    if variables:
        agents = _interpolate_deep(agents, variables)

    return agents


def load_tasks_yaml(
    path: str | Path,
    *,
    variables: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load task definitions from a YAML file.

    Expected format:
        task_name:
          description: Analyze {topic} data
          agent: data_analyst
          expected_output: Analysis report
          context: [fetch_data]

    Returns a list of dicts with 'name', 'description', 'agent', etc.
    """
    _require_yaml()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Tasks YAML not found: {p}")

    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"tasks.yaml must be a YAML mapping, got {type(raw).__name__}")

    tasks: list[dict[str, Any]] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            logger.warning("Skipping task %r: value is not a mapping", name)
            continue

        task: dict[str, Any] = {"name": str(name)}
        task["description"] = spec.get("description", "")
        task["agent"] = spec.get("agent", "")
        task["expected_output"] = spec.get("expected_output", "")
        task["context"] = spec.get("context", [])

        for key in spec:
            if key not in ("description", "agent", "expected_output", "context"):
                task[key] = spec[key]

        tasks.append(task)

    if variables:
        tasks = _interpolate_deep(tasks, variables)

    return tasks


def auto_discover_yaml(workspace_dir: str | Path) -> dict[str, Path | None]:
    """Check for agents.yaml and tasks.yaml in a workspace directory.

    Returns {"agents": Path or None, "tasks": Path or None}.
    """
    ws = Path(workspace_dir)
    result: dict[str, Path | None] = {"agents": None, "tasks": None}

    for name in ("agents.yaml", "agents.yml"):
        p = ws / name
        if p.exists():
            result["agents"] = p
            break

    for name in ("tasks.yaml", "tasks.yml"):
        p = ws / name
        if p.exists():
            result["tasks"] = p
            break

    return result
