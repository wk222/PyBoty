"""Runtime configuration — re-exports from the moved implementation."""

from core.systems.runtime.config_impl import (
    get_agent_control_config,
    get_channels_config,
    get_config,
    get_llm_config,
    get_llm_fallback_config,
    get_observability_config,
    get_openclaw_compat_config,
    get_rag_config,
    reload_config,
    resolve_config_path,
    save_config,
)

__all__ = [
    "get_agent_control_config",
    "get_channels_config",
    "get_config",
    "get_llm_config",
    "get_llm_fallback_config",
    "get_openclaw_compat_config",
    "get_observability_config",
    "get_rag_config",
    "reload_config",
    "resolve_config_path",
    "save_config",
]
