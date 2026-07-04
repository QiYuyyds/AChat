"""on_message_end preference routing: single extraction pass.

LLM extraction is the primary path; the coarse rule pass is only a fallback
when no generate_fn is configured. Prevents duplicate preference keys.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.memory_service import MemoryService


def _make_svc():
    from app.config import Settings
    return MemoryService(Settings(database_url="sqlite+aiosqlite:///:memory:"))


class _Sess:
    def __init__(self): self.added = []
    def add(self, row): self.added.append(row)
    async def execute(self, stmt): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass


@pytest.mark.asyncio
async def test_llm_primary_skips_rule_pass():
    svc = _make_svc()
    svc.set_generate_fn(lambda s, u: "{}")
    svc._safe_extract_preference = AsyncMock()
    svc._safe_llm_extract_preference = AsyncMock()
    with patch("app.memory.memory_service.get_db", MagicMock(return_value=_Sess())):
        await svc.on_message_end("user", "我喜欢周润发，喜欢rap，爱吃大白菜")
        await asyncio.sleep(0)  # let the fire-and-forget LLM task register
    assert svc._safe_llm_extract_preference.called
    assert not svc._safe_extract_preference.called, "rule pass must not run when LLM available"


@pytest.mark.asyncio
async def test_rule_fallback_without_llm():
    svc = _make_svc()  # no generate_fn injected
    svc._safe_extract_preference = AsyncMock()
    svc._safe_llm_extract_preference = AsyncMock()
    with patch("app.memory.memory_service.get_db", MagicMock(return_value=_Sess())):
        await svc.on_message_end("user", "我叫张三")
    assert svc._safe_extract_preference.called
    assert not svc._safe_llm_extract_preference.called
