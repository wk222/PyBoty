"""User directive parser — extract in-band directives from messages.

Users can embed
directives in their messages to control agent behaviour:

  * ``@think`` — enable chain-of-thought / reasoning mode
  * ``@verbose`` — request detailed, verbose output
  * ``@brief`` — request concise, terse output
  * ``@exec`` — allow code execution in response
  * ``@no-tools`` — disable tool usage for this turn
  * ``@model:xxx`` — override the model for this turn
  * ``@temp:0.5`` — override temperature
  * ``@lang:en`` — force response language

Directives are case-insensitive, can appear anywhere in the message,
and are stripped from the text before it reaches the LLM.

Usage::

    from core.systems.runtime.directive_parser import parse_directives

    result = parse_directives("@think @verbose Tell me about Python")
    # result.clean_text == "Tell me about Python"
    # result.directives == {"think": True, "verbose": True}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_DIRECTIVE_PATTERN = re.compile(
    r"@(think|verbose|brief|exec|no-tools|no_tools|model|temp|lang|json|markdown|stream|debug)"
    r"(?::([^\s]+))?",
    re.IGNORECASE,
)


@dataclass
class DirectiveResult:
    """Parsed directives extracted from a user message."""
    clean_text: str = ""
    directives: dict[str, Any] = field(default_factory=dict)
    raw_directives: list[str] = field(default_factory=list)

    @property
    def has_directives(self) -> bool:
        return bool(self.directives)

    def get(self, key: str, default: Any = None) -> Any:
        return self.directives.get(key, default)

    @property
    def think(self) -> bool:
        return bool(self.directives.get("think"))

    @property
    def verbose(self) -> bool:
        return bool(self.directives.get("verbose"))

    @property
    def brief(self) -> bool:
        return bool(self.directives.get("brief"))

    @property
    def exec_allowed(self) -> bool:
        return bool(self.directives.get("exec"))

    @property
    def no_tools(self) -> bool:
        return bool(self.directives.get("no-tools") or self.directives.get("no_tools"))

    @property
    def model_override(self) -> str | None:
        return self.directives.get("model")

    @property
    def temperature_override(self) -> float | None:
        val = self.directives.get("temp")
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def language(self) -> str | None:
        return self.directives.get("lang")

    @property
    def output_format(self) -> str | None:
        """Return 'json' or 'markdown' if requested."""
        if self.directives.get("json"):
            return "json"
        if self.directives.get("markdown"):
            return "markdown"
        return None


def parse_directives(text: str) -> DirectiveResult:
    """Extract all directives from *text* and return clean text + parsed directives."""
    result = DirectiveResult()
    raw_matches: list[str] = []

    def _replacer(match: re.Match) -> str:
        name = match.group(1).lower().replace("_", "-")
        value = match.group(2)
        raw_matches.append(match.group(0))

        if name in ("think", "verbose", "brief", "exec", "no-tools", "json", "markdown", "stream", "debug"):
            result.directives[name] = True
        elif name in ("model", "temp", "lang") and value:
            result.directives[name] = value
        elif name in ("model", "temp", "lang"):
            pass

        return ""

    cleaned = _DIRECTIVE_PATTERN.sub(_replacer, text)
    result.clean_text = cleaned.strip()
    result.clean_text = re.sub(r"\s{2,}", " ", result.clean_text)
    result.raw_directives = raw_matches
    return result


def apply_directives_to_config(
    directives: DirectiveResult,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Apply parsed directives to a mutable agent config dict.

    Modifies *config* in place and returns it for chaining.
    """
    if directives.model_override:
        config["model_override"] = directives.model_override

    if directives.temperature_override is not None:
        config["temperature_override"] = directives.temperature_override

    if directives.think:
        config["chain_of_thought"] = True

    if directives.verbose:
        config["verbose"] = True

    if directives.brief:
        config["verbose"] = False
        config["brief"] = True

    if directives.no_tools:
        config["disable_tools"] = True

    if directives.exec_allowed:
        config["allow_exec"] = True

    if directives.language:
        config["response_language"] = directives.language

    if directives.output_format:
        config["output_format"] = directives.output_format

    if directives.get("debug"):
        config["debug"] = True

    if directives.get("stream"):
        config["stream"] = True

    return config
