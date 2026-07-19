"""Validate adapter key order still holds with cloud-fetched settings (task 5.2)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.agent_runner import _pick_settings_key


def test_pick_settings_key_prefers_settings_then_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = SimpleNamespace(
        openai_api_key="sk-from-cloud",
        anthropic_api_key=None,
        deepseek_api_key=None,
        ark_api_key=None,
    )
    agent = SimpleNamespace(adapter_name="custom", model_provider="openai")
    assert _pick_settings_key(settings, agent) == "sk-from-cloud"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    settings_empty = SimpleNamespace(
        openai_api_key=None,
        anthropic_api_key=None,
        deepseek_api_key=None,
        ark_api_key=None,
    )
    assert _pick_settings_key(settings_empty, agent) == "sk-env"


def test_pick_settings_key_anthropic_chain(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    settings = SimpleNamespace(
        openai_api_key=None,
        anthropic_api_key="sk-ant-cloud",
        deepseek_api_key=None,
        ark_api_key=None,
    )
    agent = SimpleNamespace(adapter_name="custom", model_provider="anthropic")
    assert _pick_settings_key(settings, agent) == "sk-ant-cloud"
