"""Tests for MemoryService.set_generate_fn — summary LLM injection.

Verifies that SessionMemory._generate_fn comes from summary_llm
(an independent cheap model), not from the main conversation model.
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_summary_env(monkeypatch):
    """Clear SUMMARY_LLM_* and DEEPSEEK_API_KEY env vars for each test."""
    for key in (
        "SUMMARY_LLM_PROVIDER",
        "SUMMARY_LLM_MODEL",
        "SUMMARY_LLM_API_KEY",
        "SUMMARY_LLM_BASE_URL",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _main_fn(s, u):
    return "main-model-output"


def _summary_fn(s, u):
    return "summary-model-output"


def _summary_fn_2(s, u):
    return "summary"


def test_session_memory_uses_summary_llm(monkeypatch):
    """set_generate_fn injects summary_llm into SessionMemory, not the main model."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "sk-summary-test")

    from app.config import Settings
    from app.memory.memory_service import MemoryService

    settings = Settings()
    svc = MemoryService(settings)

    with patch("app.memory.memory_service.get_summary_generate_fn") as mock_summary:
        mock_summary.return_value = _summary_fn

        svc.set_generate_fn(_main_fn)

        assert svc._generate_fn is _main_fn
        assert svc.auto_memory._generate_fn is _main_fn
        assert svc.auto_dream._generate_fn is _main_fn
        assert svc.session_memory._generate_fn is _summary_fn


def test_session_memory_not_overridden_when_no_summary_key(monkeypatch):
    """When no summary LLM key is available, SessionMemory keeps its default (None)."""
    monkeypatch.delenv("SUMMARY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    from app.config import Settings
    from app.memory.memory_service import MemoryService

    settings = Settings()
    svc = MemoryService(settings)

    with patch("app.memory.memory_service.get_summary_generate_fn") as mock_summary:
        mock_summary.return_value = None

        svc.set_generate_fn(_main_fn)

        assert svc._generate_fn is _main_fn
        assert svc.auto_memory._generate_fn is _main_fn
        assert svc.auto_dream._generate_fn is _main_fn
        assert svc.session_memory._generate_fn is None


def test_summary_llm_called_once_per_set_generate_fn(monkeypatch):
    """get_summary_generate_fn is called exactly once per set_generate_fn call."""
    monkeypatch.setenv("SUMMARY_LLM_API_KEY", "sk-summary-test")

    from app.config import Settings
    from app.memory.memory_service import MemoryService

    settings = Settings()
    svc = MemoryService(settings)

    with patch("app.memory.memory_service.get_summary_generate_fn") as mock_summary:
        mock_summary.return_value = _summary_fn_2
        svc.set_generate_fn(_main_fn)
        assert mock_summary.call_count == 1
