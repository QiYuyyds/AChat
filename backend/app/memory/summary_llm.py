"""Independent summary LLM — cheap model for SessionMemory extraction.

Reads ``SUMMARY_LLM_*`` env vars (fallback to ``DEEPSEEK_API_KEY``) and
returns a synchronous ``generate(system_prompt, user_msg) -> str`` callable
suitable for injection into ``SessionMemory.set_generate_fn``.

When no API key is available, returns ``None`` so SessionMemory degrades to
no-op without blocking the main conversation.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from openai import OpenAI

logger = logging.getLogger(__name__)


def get_summary_generate_fn() -> Callable[[str, str], str] | None:
    """Build a generate function backed by a cheap summary LLM.

    Resolution chain:
    1. ``SUMMARY_LLM_API_KEY`` env var
    2. ``DEEPSEEK_API_KEY`` env var
    3. Return ``None`` (SessionMemory becomes no-op)

    The returned callable uses the ``openai`` SDK with ``max_tokens=2048``
    and ``temperature=0.3``. The client is constructed with ``max_retries=1``.
    """
    api_key = os.environ.get("SUMMARY_LLM_API_KEY", "").strip()
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "[summary-llm] No SUMMARY_LLM_API_KEY or DEEPSEEK_API_KEY found; "
            "session memory extraction disabled"
        )
        return None

    model = os.environ.get("SUMMARY_LLM_MODEL", "deepseek-chat").strip()
    base_url = os.environ.get("SUMMARY_LLM_BASE_URL", "").strip()
    # provider kept for future extensibility; currently all go through openai SDK
    provider = os.environ.get("SUMMARY_LLM_PROVIDER", "deepseek").strip()
    logger.info(
        "[summary-llm] Initialised: provider=%s model=%s base_url=%s",
        provider,
        model,
        base_url or "(default)",
    )

    client_kwargs: dict[str, object] = {"api_key": api_key, "max_retries": 1}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    def generate(system_prompt: str, user_msg: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""

    return generate
