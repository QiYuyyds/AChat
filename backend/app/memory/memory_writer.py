"""Memory writer — LLM-based memory extraction and classification from assistant replies.

Ported from AGI-memory ``internal/agent/memory_writer.py``.
Adapted for AgentHub's async architecture (no threading, uses asyncio.create_task).

Key functions:
  - ``classify_memory_content(key, value)``: Rule-based classification
    (identity/preference/tool_failure/policy).
  - ``extract_memory_from_reply(generate_fn, embed_fn, ltm, content)``:
    Async extraction of k-v facts from assistant replies via LLM,
    followed by classification and ``ltm.store_classified()``.
"""

import json
import logging
import re
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _strip_code_fence(raw: str) -> str:
    """Remove markdown code fences from LLM output."""
    raw = (raw or "").strip()
    raw = re.sub(r"^```json", "", raw)
    raw = re.sub(r"^```", "", raw)
    raw = re.sub(r"```$", "", raw)
    return raw.strip()


def _contains_any(s: str, *subs: str) -> bool:
    """Check if string contains any of the given substrings."""
    return any(sub in s for sub in subs)


# ── Rule-based classification ──────────────────────────────────────────────


def classify_memory_content(key: str, value: str) -> Tuple[str, List[str], str]:
    """Classify memory content using rules.

    Returns (category, tags, slot_hint).
    Returns empty strings if no rule matches (caller should use LLM fallback).

    Ported from AGI-memory classify_memory_content().
    """
    combined = f"{key}{value}"
    if _contains_any(combined, "叫", "名字", "姓名", "是我", "我是"):
        return "identity", ["name"], "profile"
    if _contains_any(combined, "喜欢", "偏好", "习惯", "爱好", "讨厌", "不喜欢", "prefer"):
        return "preference", ["preference"], "profile"
    if _contains_any(combined, "工具", "失败", "错误", "报错", "异常", "error", "bug"):
        return "tool_failure", ["tool", "error"], "tool_state"
    if _contains_any(combined, "禁止", "不要", "不能", "必须", "强制", "must", "never"):
        return "policy", ["constraint"], "constraints"
    return "", [], ""


# ── LLM classification prompt ─────────────────────────────────────────────────

_CLASSIFY_SYSTEM_PROMPT = (
    "你是一个记忆分类助手。请将给定的内容分类到以下 7 个类别之一，并给出标签和槽位提示。\n"
    "类别（category）：identity（身份信息）、preference（偏好）、fact（事实）、"
    "episodic（事件）、tool_failure（工具失败）、policy（策略约束）、general（通用）。\n"
    "槽位提示（slot_hint）：profile、planner、task_memory、tool_state、constraints、recall_memory。\n"
    '输出 JSON：{"category":"...","tags":["tag1"],"slot_hint":"..."}\n'
    "只输出 JSON，不要有其他内容。"
)


async def llm_classify_memory(
    generate_fn: Callable,
    content: str,
) -> Tuple[str, List[str], str]:
    """Classify memory content using LLM fallback.

    Requests JSON output with category, tags, slot_hint.
    Strips code fences; falls back to ("general", [], "") on parse failure.
    """
    if not generate_fn or not content:
        return "general", [], ""
    try:
        raw = generate_fn(_CLASSIFY_SYSTEM_PROMPT, content)
    except Exception as e:
        logger.warning("llm_classify_memory LLM call failed: %s", e)
        return "general", [], ""

    raw = _strip_code_fence(raw)
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.debug("llm_classify_memory: LLM output is not valid JSON: %s", raw[:100])
        return "general", [], ""

    if not isinstance(parsed, dict):
        return "general", [], ""

    valid_categories = {
        "identity", "preference", "fact", "episodic",
        "tool_failure", "policy", "general",
    }
    valid_slots = {
        "profile", "planner", "task_memory",
        "tool_state", "constraints", "recall_memory",
    }

    category = str(parsed.get("category", "general")).strip()
    category_invalid = category not in valid_categories
    if category_invalid:
        category = "general"

    tags_raw = parsed.get("tags", [])
    tags = [str(t) for t in tags_raw if t] if isinstance(tags_raw, list) else []

    slot_hint = str(parsed.get("slot_hint", "")).strip()
    # If category was invalid, don't trust the slot_hint either
    if slot_hint not in valid_slots or category_invalid:
        slot_hint = ""

    return category, tags, slot_hint


# ── LLM extraction prompt ──────────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = (
    "你是一个信息提取助手。从下面的AI回复中提取值得长期记住的客观事实或用户偏好信息。\n"
    "只提取明确的、非临时性的信息，忽略对话上下文和临时细节。\n"
    "输出 JSON 对象（key为信息名称，value为具体值），如果没有值得记忆的信息则输出 {}。\n"
    "只输出 JSON，不要有其他内容。"
)


# ── Async extraction from assistant reply ───────────────────────────────────


async def extract_memory_from_reply(
    generate_fn: Callable,
    embed_fn: Optional[Callable],
    ltm,
    content: str,
) -> None:
    """Extract k-v facts from assistant reply using LLM, classify, and store.

    Workflow:
      1. LLM extracts key-value facts from the reply.
      2. Each fact is classified via ``classify_memory_content()``.
      3. Embedding is computed for each fact.
      4. Facts are stored via ``ltm.store_classified()`` with dedup.

    Args:
        generate_fn: LLM generate function (system_prompt, user_msg) -> str
        embed_fn: Optional embedding function (text) -> list[float]
        ltm: LongTerm memory instance with ``store_classified()`` method
        content: The assistant reply text
    """
    if not content or not generate_fn:
        return

    # Step 1: LLM extraction
    prompt = f"回复：{content}"
    try:
        raw = generate_fn(_EXTRACTION_SYSTEM_PROMPT, prompt)
    except Exception as e:
        logger.warning("Memory extraction LLM call failed: %s", e)
        return

    raw = _strip_code_fence(raw)
    try:
        kvs = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.debug("Memory extraction: LLM output is not valid JSON")
        return

    if not isinstance(kvs, dict) or not kvs:
        return

    # Step 2-4: classify, embed, store each fact
    for k, v in kvs.items():
        if not k or v in (None, ""):
            continue

        fact_content = f"用户{k}: {v}"
        category, tags, slot_hint = classify_memory_content(str(k), str(v))
        if not category:
            # Rule classification missed — try LLM fallback
            category, tags, slot_hint = await llm_classify_memory(
                generate_fn, fact_content,
            )

        # Compute embedding
        emb = None
        if embed_fn:
            try:
                emb = embed_fn(fact_content)
            except Exception as e:
                logger.warning("Memory extraction embed failed: %s", e)

        importance = 0.7

        # Store via store_classified (with cosine dedup)
        try:
            inserted = await ltm.store_classified(
                fact_content,
                importance,
                emb,
                category,
                tags,
                slot_hint,
            )
            logger.info(
                "Memory extracted: %s = %s (category=%s, inserted=%s)",
                k, v, category, inserted,
            )
        except Exception as e:
            logger.warning("Memory extraction store failed: %s", e)
