"""Tests for core.model_resolver — multi-provider model resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models import BaseChatModel

from core.systems.runtime import (
    ModelProviderError,
    ResolvedModel,
    _parse_spec,
    list_all_providers,
    list_available_providers,
    resolve_model,
)


def _fake_chat_model(**attrs) -> MagicMock:
    """Create a MagicMock that passes isinstance(x, BaseChatModel)."""
    m = MagicMock(spec=BaseChatModel)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class TestParseSpec:
    def test_provider_model_format(self):
        provider, model = _parse_spec("anthropic:claude-sonnet-4-20250514")
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-20250514"

    def test_plain_model_defaults_to_openai(self):
        provider, model = _parse_spec("gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"

    def test_provider_with_spaces(self):
        provider, model = _parse_spec("  google : gemini-2.0-flash  ")
        assert provider == "google"
        assert model == "gemini-2.0-flash"

    def test_empty_model_raises(self):
        with pytest.raises(ModelProviderError, match="Empty model name"):
            _parse_spec("openai:")

    def test_multiple_colons(self):
        provider, model = _parse_spec("openai:ft:gpt-4o:org:custom")
        assert provider == "openai"
        assert model == "ft:gpt-4o:org:custom"


class TestResolveModelPrebuilt:
    def test_passthrough_prebuilt_model(self):
        mock_model = _fake_chat_model(model_name="test-model")
        result = resolve_model(mock_model)
        assert isinstance(result, ResolvedModel)
        assert result.model is mock_model
        assert result.provider == "prebuilt"
        assert result.model_name == "test-model"

    def test_prebuilt_model_without_model_name(self):
        mock_model = _fake_chat_model(model="fallback-name")
        if hasattr(mock_model, "model_name"):
            del mock_model.model_name
        result = resolve_model(mock_model)
        assert result.provider == "prebuilt"


class TestResolveModelString:
    @patch("core.model_resolver._check_provider_available")
    @patch("core.model_resolver._build_model_from_provider")
    def test_openai_string(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        result = resolve_model("gpt-4o", temperature=0.5)
        mock_check.assert_called_once_with("openai")
        mock_build.assert_called_once_with("openai", "gpt-4o", temperature=0.5, api_key=None, base_url=None)
        assert result.provider == "openai"
        assert result.model_name == "gpt-4o"

    @patch("core.model_resolver._check_provider_available")
    @patch("core.model_resolver._build_model_from_provider")
    def test_anthropic_string(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        result = resolve_model("anthropic:claude-sonnet-4-20250514", api_key="sk-test")
        mock_check.assert_called_once_with("anthropic")
        assert result.provider == "anthropic"
        assert result.model_name == "claude-sonnet-4-20250514"


class TestResolveModelDict:
    @patch("core.model_resolver._check_provider_available")
    @patch("core.model_resolver._build_model_from_provider")
    def test_dict_with_provider(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        spec = {"provider": "google", "model": "gemini-2.0-flash", "temperature": 0.3}
        result = resolve_model(spec)
        mock_check.assert_called_once_with("google")
        assert result.provider == "google"
        assert result.model_name == "gemini-2.0-flash"

    @patch("core.model_resolver._check_provider_available")
    @patch("core.model_resolver._build_model_from_provider")
    def test_dict_without_provider_uses_base_url(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        spec = {"model": "custom-model", "api_base": "http://localhost:8080/v1"}
        result = resolve_model(spec)
        mock_check.assert_called_once_with("openai")
        assert result.provider == "openai"

    def test_dict_missing_model_raises(self):
        with pytest.raises(ModelProviderError, match="must include 'model'"):
            resolve_model({"provider": "openai"})

    @patch("core.model_resolver._check_provider_available")
    @patch("core.model_resolver._build_model_from_provider")
    def test_dict_provider_model_format(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        spec = {"model": "anthropic:claude-sonnet-4-20250514"}
        result = resolve_model(spec)
        mock_check.assert_called_once_with("anthropic")
        assert result.model_name == "claude-sonnet-4-20250514"

    @patch("core.model_resolver._check_provider_available")
    @patch("core.model_resolver._build_model_from_provider")
    def test_dict_api_base_alias(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        spec = {"model": "gpt-4o", "api_base": "http://proxy/v1"}
        resolve_model(spec)
        call_kwargs = mock_build.call_args
        assert call_kwargs.kwargs.get("base_url") == "http://proxy/v1"


class TestResolveModelErrors:
    def test_unknown_provider(self):
        with pytest.raises(ModelProviderError, match="Unknown provider"):
            resolve_model("nonexistent:model-x")

    def test_unsupported_spec_type(self):
        with pytest.raises(ModelProviderError, match="Unsupported spec type"):
            resolve_model(42)


class TestProviderListing:
    @patch("core.model_resolver.importlib.import_module")
    def test_list_available(self, mock_import):
        mock_import.side_effect = lambda pkg: None if pkg == "langchain_openai" else (_ for _ in ()).throw(ImportError)
        result = list_available_providers()
        assert "openai" in result

    @patch("core.model_resolver.importlib.import_module")
    def test_list_all(self, mock_import):
        mock_import.side_effect = lambda pkg: None if pkg == "langchain_openai" else (_ for _ in ()).throw(ImportError)
        result = list_all_providers()
        assert result["openai"] is True
        assert result["anthropic"] is False


class TestCheckProviderAvailable:
    def test_missing_package_gives_install_hint(self):
        with patch("core.model_resolver.importlib.import_module", side_effect=ImportError):
            with pytest.raises(ModelProviderError, match="pip install langchain-anthropic"):
                resolve_model("anthropic:claude-sonnet-4-20250514")
