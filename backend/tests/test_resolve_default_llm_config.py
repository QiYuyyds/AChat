"""Tests for settings_service.resolve_default_llm_config fallback chain.

Covers the five resolution paths:
1. llm_api_key fully configured (key + url + model)
2. llm_api_key only (url/model fall back to defaults)
3. openai_api_key fallback
4. deepseek_api_key fallback
5. no keys at all → None

All settings fields are pinned explicitly so shell environment variables
(LLM_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY ...) cannot leak in.
"""

from app.config import Settings
from app.services.settings_service import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_OPENAI_BASE_URL,
    resolve_default_llm_config,
)


def _settings(**overrides) -> Settings:
    fields = {
        "llm_api_key": None,
        "llm_api_url": None,
        "llm_model": None,
        "openai_api_key": None,
        "deepseek_api_key": None,
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def test_llm_api_key_fully_configured():
    settings = _settings(
        llm_api_key="sk-llm",
        llm_api_url="https://dashscope.example.com/compatible-mode/v1",
        llm_model="qwen-plus",
    )
    assert resolve_default_llm_config(settings) == (
        "sk-llm",
        "https://dashscope.example.com/compatible-mode/v1",
        "qwen-plus",
    )


def test_llm_api_key_only_uses_default_url_and_model():
    settings = _settings(llm_api_key="sk-llm")
    assert resolve_default_llm_config(settings) == (
        "sk-llm",
        DEFAULT_OPENAI_BASE_URL,
        DEFAULT_FALLBACK_MODEL,
    )


def test_openai_fallback():
    settings = _settings(openai_api_key="sk-openai")
    assert resolve_default_llm_config(settings) == (
        "sk-openai",
        DEFAULT_OPENAI_BASE_URL,
        DEFAULT_FALLBACK_MODEL,
    )


def test_deepseek_fallback():
    settings = _settings(deepseek_api_key="sk-deepseek")
    assert resolve_default_llm_config(settings) == (
        "sk-deepseek",
        DEFAULT_DEEPSEEK_BASE_URL,
        DEFAULT_DEEPSEEK_MODEL,
    )


def test_no_keys_returns_none():
    assert resolve_default_llm_config(_settings()) is None
