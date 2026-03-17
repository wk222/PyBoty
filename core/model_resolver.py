"""Multi-provider LLM model resolver.

Supports multiple model specification formats:
  - "provider:model"  e.g. "openai:gpt-4o", "anthropic:claude-sonnet-4-20250514"
  - Plain model name   e.g. "gpt-4o" (uses configured provider or defaults to openai)
  - Dict config        e.g. {"provider": "openai", "model": "gpt-4o", "temperature": 0.2}
  - Pre-built BaseChatModel instance (passed through unchanged)

When a provider-specific package is missing, raises ModelProviderError with install hint.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

_PROVIDER_PACKAGES: dict[str, str] = {
    "openai": "langchain_openai",
    "anthropic": "langchain_anthropic",
    "google": "langchain_google_genai",
    "ollama": "langchain_ollama",
    "bedrock": "langchain_aws",
    "mistral": "langchain_mistralai",
    "groq": "langchain_groq",
    "fireworks": "langchain_fireworks",
    "deepseek": "langchain_openai",
    "together": "langchain_together",
}

_PROVIDER_INSTALL_HINTS: dict[str, str] = {
    "openai": "pip install langchain-openai",
    "anthropic": "pip install langchain-anthropic",
    "google": "pip install langchain-google-genai",
    "ollama": "pip install langchain-ollama",
    "bedrock": "pip install langchain-aws",
    "mistral": "pip install langchain-mistralai",
    "groq": "pip install langchain-groq",
    "fireworks": "pip install langchain-fireworks",
    "together": "pip install langchain-together",
}

_PROVIDER_CHAT_CLASSES: dict[str, tuple[str, str]] = {
    "openai": ("langchain_openai", "ChatOpenAI"),
    "anthropic": ("langchain_anthropic", "ChatAnthropic"),
    "google": ("langchain_google_genai", "ChatGoogleGenerativeAI"),
    "ollama": ("langchain_ollama", "ChatOllama"),
    "bedrock": ("langchain_aws", "ChatBedrockConverse"),
    "mistral": ("langchain_mistralai", "ChatMistralAI"),
    "groq": ("langchain_groq", "ChatGroq"),
    "fireworks": ("langchain_fireworks", "ChatFireworks"),
    "deepseek": ("langchain_openai", "ChatOpenAI"),
    "together": ("langchain_together", "ChatTogether"),
}


class ModelProviderError(Exception):
    """Raised when a model provider package is unavailable or misconfigured."""


@dataclass
class ResolvedModel:
    """Result of model resolution, carrying the model and metadata."""

    model: BaseChatModel
    provider: str
    model_name: str
    extras: dict[str, Any] = field(default_factory=dict)


def _parse_spec(spec: str) -> tuple[str, str]:
    """Parse 'provider:model' into (provider, model). Plain name -> ('openai', name)."""
    if ":" in spec:
        provider, _, model_name = spec.partition(":")
        provider = provider.strip().lower()
        model_name = model_name.strip()
        if not model_name:
            raise ModelProviderError(f"Empty model name in spec: {spec!r}")
        return provider, model_name
    return "openai", spec.strip()


def _check_provider_available(provider: str) -> None:
    """Verify that the required package for a provider is installed."""
    pkg = _PROVIDER_PACKAGES.get(provider)
    if pkg is None:
        raise ModelProviderError(f"Unknown provider: {provider!r}. Supported: {', '.join(sorted(_PROVIDER_PACKAGES))}")
    try:
        importlib.import_module(pkg)
    except ImportError:
        hint = _PROVIDER_INSTALL_HINTS.get(provider, f"pip install {pkg}")
        raise ModelProviderError(f"Provider {provider!r} requires package {pkg!r}. Install with: {hint}") from None


def _build_model_from_provider(
    provider: str,
    model_name: str,
    **kwargs: Any,
) -> BaseChatModel:
    """Instantiate the chat model class for a given provider."""
    entry = _PROVIDER_CHAT_CLASSES.get(provider)
    if entry is None:
        raise ModelProviderError(f"No chat class mapping for provider: {provider!r}")

    module_path, class_name = entry
    mod = importlib.import_module(module_path)
    chat_cls = getattr(mod, class_name)

    build_kwargs: dict[str, Any] = {"model": model_name}

    if provider == "deepseek":
        build_kwargs.setdefault("base_url", "https://api.deepseek.com/v1")

    for key in ("temperature", "api_key", "base_url", "max_tokens", "streaming"):
        if key in kwargs and kwargs[key] is not None:
            build_kwargs[key] = kwargs[key]

    extra_keys = set(kwargs) - {"temperature", "api_key", "base_url", "max_tokens", "streaming", "provider", "model"}
    for key in extra_keys:
        if kwargs[key] is not None:
            build_kwargs[key] = kwargs[key]

    return chat_cls(**build_kwargs)


def resolve_model(
    spec: str | dict[str, Any] | BaseChatModel,
    *,
    temperature: float = 0.7,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> ResolvedModel:
    """Resolve a model specification into a usable BaseChatModel.

    Args:
        spec: Model specification — string, dict, or pre-built model.
        temperature: Default temperature (overridden by spec if present).
        api_key: API key (overridden by spec if present).
        base_url: API base URL (overridden by spec if present).
        **kwargs: Additional provider-specific arguments.

    Returns:
        ResolvedModel with the instantiated model and metadata.
    """
    if isinstance(spec, BaseChatModel):
        model_name = getattr(spec, "model_name", getattr(spec, "model", "unknown"))
        return ResolvedModel(model=spec, provider="prebuilt", model_name=str(model_name))

    if isinstance(spec, dict):
        spec_copy = dict(spec)
        provider = spec_copy.pop("provider", None)
        model_name = spec_copy.pop("model", None)
        if model_name is None:
            raise ModelProviderError("Dict spec must include 'model' key")

        resolved_temp = spec_copy.pop("temperature", temperature)
        resolved_key = spec_copy.pop("api_key", api_key)
        resolved_base = spec_copy.pop("api_base", spec_copy.pop("base_url", base_url))

        if provider is None:
            if resolved_base:
                provider = "openai"
            else:
                provider, model_name = _parse_spec(model_name)

        merged_kwargs = {**kwargs, **spec_copy}
        _check_provider_available(provider)
        model = _build_model_from_provider(
            provider,
            model_name,
            temperature=resolved_temp,
            api_key=resolved_key,
            base_url=resolved_base,
            **merged_kwargs,
        )
        return ResolvedModel(model=model, provider=provider, model_name=model_name)

    if isinstance(spec, str):
        provider, model_name = _parse_spec(spec)
        _check_provider_available(provider)
        model = _build_model_from_provider(
            provider,
            model_name,
            temperature=temperature,
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )
        return ResolvedModel(model=model, provider=provider, model_name=model_name)

    raise ModelProviderError(f"Unsupported spec type: {type(spec).__name__}")


def list_available_providers() -> list[str]:
    """Return providers whose packages are installed."""
    available = []
    for provider, pkg in sorted(_PROVIDER_PACKAGES.items()):
        try:
            importlib.import_module(pkg)
            available.append(provider)
        except ImportError:
            pass
    return available


def list_all_providers() -> dict[str, bool]:
    """Return all known providers with availability status."""
    result = {}
    for provider, pkg in sorted(_PROVIDER_PACKAGES.items()):
        try:
            importlib.import_module(pkg)
            result[provider] = True
        except ImportError:
            result[provider] = False
    return result
