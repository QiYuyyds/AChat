"""Tests for summary_llm — independent summary model configuration.

Verifies:
- Environment variable parsing (provider, model, base_url)
- Fallback chain: SUMMARY_LLM_API_KEY → DEEPSEEK_API_KEY → None
- No key available → returns None with warning
- Generate function calls OpenAI SDK with correct params
"""

from unittest.mock import patch

import pytest

from app.memory.summary_llm import get_summary_generate_fn


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Clear all SUMMARY_LLM_* and DEEPSEEK_API_KEY env vars for each test."""
    for key in (
        "SUMMARY_LLM_PROVIDER",
        "SUMMARY_LLM_MODEL",
        "SUMMARY_LLM_API_KEY",
        "SUMMARY_LLM_BASE_URL",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


# ─── no key → None ─────────────────────────────────────────────────────────


def test_returns_none_when_no_key(monkeypatch):
    """No SUMMARY_LLM_API_KEY and no DEEPSEEK_API_KEY → None."""
    monkeypatch.delenv("SUMMARY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    result = get_summary_generate_fn()
    assert result is None


def test_returns_none_when_keys_empty(monkeypatch):
    """Empty string keys → None (treated as missing)."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    result = get_summary_generate_fn()
    assert result is None


# ─── fallback chain ──────────────────────────────────────────────────────────


def test_uses_summary_llm_key_when_set(monkeypatch):
    """SUMMARY_LLM_API_KEY takes priority over DEEPSEEK_API_KEY."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "sk-summary-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-key")

    with patch("app.memory.summary_llm.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = _mock_response("summary")

        fn = get_summary_generate_fn()
        assert fn is not None

        result = fn("system prompt", "user msg")

        # Verify OpenAI was called with the SUMMARY key
        call_kwargs = mock_openai.call_args
        assert call_kwargs.kwargs.get("api_key") == "sk-summary-key"
        assert result == "summary"


def test_falls_back_to_deepseek_key(monkeypatch):
    """No SUMMARY_LLM_API_KEY → falls back to DEEPSEEK_API_KEY."""
    monkeypatch.delenv("SUMMARY_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-fallback")

    with patch("app.memory.summary_llm.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = _mock_response("ok")

        fn = get_summary_generate_fn()
        assert fn is not None

        fn("sys", "usr")

        call_kwargs = mock_openai.call_args
        assert call_kwargs.kwargs.get("api_key") == "sk-deepseek-fallback"


# ─── env var parsing ─────────────────────────────────────────────────────────


def test_default_model_and_provider(monkeypatch):
    """Default model=deepseek-chat, provider=deepseek when not set."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "sk-test")

    with patch("app.memory.summary_llm.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = _mock_response("ok")

        fn = get_summary_generate_fn()
        fn("sys", "usr")

        create_kwargs = mock_client.chat.completions.create.call_args
        assert create_kwargs.kwargs.get("model") == "deepseek-chat"
        assert create_kwargs.kwargs.get("max_tokens") == 2048
        assert create_kwargs.kwargs.get("temperature") == 0.3
        assert "max_retries" not in create_kwargs.kwargs

        client_kwargs = mock_openai.call_args
        assert client_kwargs.kwargs.get("max_retries") == 1


def test_custom_model_and_base_url(monkeypatch):
    """Custom SUMMARY_LLM_MODEL and SUMMARY_LLM_BASE_URL are respected."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("SUMMARY_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("SUMMARY_LLM_BASE_URL", "https://api.example.com/v1")

    with patch("app.memory.summary_llm.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = _mock_response("ok")

        fn = get_summary_generate_fn()
        fn("sys", "usr")

        call_kwargs = mock_openai.call_args
        assert call_kwargs.kwargs.get("base_url") == "https://api.example.com/v1"

        create_kwargs = mock_client.chat.completions.create.call_args
        assert create_kwargs.kwargs.get("model") == "gpt-4o-mini"


def test_base_url_not_passed_when_empty(monkeypatch):
    """Empty SUMMARY_LLM_BASE_URL → base_url not passed to OpenAI client."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "sk-test")
    monkeypatch.delenv("SUMMARY_LLM_BASE_URL", raising=False)

    with patch("app.memory.summary_llm.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = _mock_response("ok")

        fn = get_summary_generate_fn()
        fn("sys", "usr")

        call_kwargs = mock_openai.call_args
        assert "base_url" not in call_kwargs.kwargs


# ─── generate function ───────────────────────────────────────────────────────


def test_generate_returns_content(monkeypatch):
    """generate() returns the content string from the OpenAI response."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "sk-test")

    with patch("app.memory.summary_llm.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = _mock_response("extracted summary text")

        fn = get_summary_generate_fn()
        result = fn("system", "user")
        assert result == "extracted summary text"


def test_generate_returns_empty_on_null_content(monkeypatch):
    """generate() returns empty string when response content is None."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "sk-test")

    with patch("app.memory.summary_llm.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = _mock_response(None)

        fn = get_summary_generate_fn()
        result = fn("system", "user")
        assert result == ""


def test_generate_passes_system_and_user_messages(monkeypatch):
    """generate() sends system_prompt and user_msg as separate messages."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "sk-test")

    with patch("app.memory.summary_llm.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = _mock_response("ok")

        fn = get_summary_generate_fn()
        fn("you are a summarizer", "summarize this conversation")

        create_kwargs = mock_client.chat.completions.create.call_args
        messages = create_kwargs.kwargs.get("messages", [])
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "you are a summarizer"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "summarize this conversation"


# ─── helpers ─────────────────────────────────────────────────────────────────


def _mock_response(content: str | None):
    """Build a mock OpenAI ChatCompletion response."""
    resp = type("Resp", (), {})()
    resp.choices = [type("Choice", (), {})()]
    resp.choices[0].message = type("Message", (), {})()
    resp.choices[0].message.content = content
    return resp
