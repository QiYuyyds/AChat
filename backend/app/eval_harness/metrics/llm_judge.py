"""LLM Judge infrastructure — protocol-injected LLM function + tolerant JSON parsing.

D2: the framework core only knows the LLMFn protocol
(async (system_prompt, user_message) -> raw text); no LLM SDK is bound here.
Assembly of a concrete implementation lives in eval_integration.config.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable

# LLM 函数协议: (system_prompt, user_message) → raw text
LLMFn = Callable[[str, str], Awaitable[str]]

# 解析失败后的最大重试次数 (每次重试重新调用 LLM)
DEFAULT_MAX_RETRIES = 2


class LLMJudgeError(Exception):
    """LLM Judge 调用/解析最终失败 (重试用尽)。"""


class LLMNotConfiguredError(Exception):
    """未注入 LLM 函数却调用了依赖 LLM 的指标 — 明确配置错误。"""


def require_llm_fn(llm_fn: LLMFn | None) -> LLMFn:
    """断言 LLM 函数已注入, 否则抛出明确配置错误 (而非静默 0 分)。"""
    if llm_fn is None:
        raise LLMNotConfiguredError(
            "LLM function not configured — inject llm_fn (eval_integration "
            "assembles one from AEVAL_JUDGE_* / eval LLM settings) or pass a "
            "stub in tests."
        )
    return llm_fn


def extract_json_object(raw: str) -> dict[str, Any] | None:
    """
    容错提取 LLM 输出中的 JSON 对象。

    容忍 ```json 围栏、前后缀说明文本; 失败返回 None。
    """
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def judge_json(
    llm_fn: LLMFn,
    system_prompt: str,
    user_prompt: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """
    调用 LLM 并解析 JSON 响应; 解析失败带完整上下文重试。

    Raises:
        LLMNotConfiguredError: llm_fn 为 None
        LLMJudgeError: 重试用尽仍无法解析 (附最后一次原始输出)
    """
    require_llm_fn(llm_fn)

    last_raw = ""
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            last_raw = await llm_fn(system_prompt, user_prompt)
        except Exception as e:
            # 传输层错误同样重试 (judge 输出不稳定/网络抖动, design §Risks)
            last_error = e
            if attempt < max_retries:
                await asyncio.sleep(0)
                continue
            raise LLMJudgeError(f"LLM call failed after {max_retries + 1} attempts: {e}") from e

        parsed = extract_json_object(last_raw)
        if parsed is not None:
            return parsed
        last_error = LLMJudgeError("response is not a parsable JSON object")

    raise LLMJudgeError(
        f"LLM judge failed after {max_retries + 1} attempts ({last_error}); "
        f"last response: {last_raw[:200]!r}"
    )
