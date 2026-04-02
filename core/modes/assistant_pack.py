"""Assistant mode pack — the simplest, interactive-only capability bundle."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.modes.pack import BaseModePack
from core.modes.profile import ModeProfile, resolve_mode_profile


def _build_profile() -> ModeProfile:
    return resolve_mode_profile("assistant")


class AssistantPack(BaseModePack):
    """Interactive chat with no durable runtime or orchestration."""

    def __init__(self) -> None:
        super().__init__(_name="assistant", _profile=_build_profile())

    # assistant has no extra init — all base defaults are sufficient

    def get_prompt_section(self, host: Any) -> str:  # noqa: ARG002
        return (
            "你当前处于人类助手模式。"
            "只在用户明确要求时才升级到更强模式的工作方式。"
        )

    def get_api_methods(self) -> dict[str, Callable[..., Any]]:
        # assistant exposes no extra public methods beyond the core PyBot API
        return {}
