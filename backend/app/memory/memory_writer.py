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

import asyncio
import json
import logging
import re
from typing import Callable, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


# Importance floor per memory category. Replaces the old hardcoded 0.7 so the
# ranking actually means something — identity/policy outrank plain facts.
_IMPORTANCE_BY_CATEGORY: Dict[str, float] = {
    "identity": 0.9,
    "policy": 0.8,
    "preference": 0.7,
    "fact": 0.5,
    "episodic": 0.4,
    "tool_failure": 0.3,
    "general": 0.3,
}


class _PreferenceLike(Protocol):
    """Minimal contract for the preference store used during double-write."""

    async def set(self, key: str, value: str) -> None: ...


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
    if _contains_any(combined, "所在地", "位于", "住在", "在城市", "在重庆", "在北京", "在上海", "在广州", "在深圳", "在成都"):
        return "identity", ["location"], "profile"
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
        raw = await asyncio.to_thread(generate_fn, _CLASSIFY_SYSTEM_PROMPT, content)
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
    "你是一个信息提取助手。从下面的AI回复中提取值得长期记住的客观事实。\n"
    "每条事实必须【自包含】：把它所描述的主体/对象/地点/时间写进 key，使其脱离对话也能读懂。\n"
    "例如天气要写明城市与日期：{\"北京湿度(2026-07-05)\": \"约71%\"}，"
    "而不是 {\"湿度\": \"约71%\"}。\n"
    "不要提取脱离主体的孤立数值，也不要提取临时对话细节。\n"
    "输出 JSON 对象（key=自包含的信息名，value=具体值），没有值得记忆的则输出 {}。\n"
    "只输出 JSON，不要有其他内容。"
)


# ── Async extraction from assistant reply ───────────────────────────────────


async def extract_memory_from_reply(
    generate_fn: Callable,
    embed_fn: Optional[Callable],
    ltm,
    content: str,
    preference: Optional[_PreferenceLike] = None,
    existing_keys: Optional[List[str]] = None,
    agent_id: str = "",
) -> None:
    """Extract k-v facts from assistant reply using LLM, classify, and store.

    Workflow:
      1. LLM extracts key-value facts from the reply.
      2. Each fact is classified via ``classify_memory_content()``.
      3. Embedding is computed for each fact.
      4. Facts are stored via ``ltm.store_classified()`` with dedup, and the
         same k-v is mirrored into the preference store (double-write, matching
         the original AGI-memory behavior).

    Args:
        generate_fn: LLM generate function (system_prompt, user_msg) -> str
        embed_fn: Optional embedding function (text) -> list[float]
        ltm: LongTerm memory instance with ``store_classified()`` method
        content: The assistant reply text
        preference: Optional preference store; when supplied, each extracted
            k-v is also written via ``preference.set()`` so rule-based
            extraction can be refined by the LLM's more precise values.
        existing_keys: Optional list of current preference keys. When provided,
            the LLM prompt includes the key list and instructs the model to
            reuse semantically equivalent existing keys.
        agent_id: When non-empty, extracted LTM facts are written with
            ``scope='agent'`` and ``agent_id`` so they are isolated per-agent.
    """
    if not content or not generate_fn:
        return

    # Step 1: LLM extraction — build dynamic prompt with existing keys
    system_prompt = _EXTRACTION_SYSTEM_PROMPT
    if existing_keys:
        keys_str = ", ".join(existing_keys)
        system_prompt = (
            system_prompt
            + f"\n4. 已有的偏好 key：{keys_str}。"
            "如果新提取的偏好与已有 key 语义相同，必须复用已有 key。"
        )
    prompt = f"回复：{content}"
    try:
        raw = await asyncio.to_thread(generate_fn, _EXTRACTION_SYSTEM_PROMPT, prompt)
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

    # Step 2-4: classify, then route each fact to a SINGLE store by category.
    # identity/preference → preference store; fact/episodic/policy/tool_failure
    # → LTM; general → dropped. No double-write.
    for k, v in kvs.items():
        if not k or v in (None, ""):
            continue

        # No hardcoded "用户" prefix: LTM facts are objective world facts
        # (weather, tool failures, policies), not user-profile attributes. The
        # user-related categories route to the preference store below with the
        # raw key, so the prefix would only ever mislabel LTM content.
        clean_key = str(k).removeprefix("用户")
        fact_content = f"{clean_key}: {v}"
        category, tags, slot_hint = classify_memory_content(str(k), str(v))
        if not category:
            # Rule classification missed — try LLM fallback
            category, tags, slot_hint = await llm_classify_memory(
                generate_fn, fact_content,
            )

        # Profile-class facts belong to the preference store only
        if category in ("identity", "preference"):
            if preference is not None:
                try:
                    await preference.set(str(k), str(v))
                except Exception as e:
                    logger.warning(
                        "Memory extraction preference.set failed for %s: %s", k, e,
                    )
            continue

        # Only recall-worthy facts go to LTM; drop general/unknown noise
        if category not in ("fact", "episodic", "policy", "tool_failure"):
            continue

        # Compute embedding off the event loop (embed_fn is a blocking client)
        emb = None
        if embed_fn:
            try:
                emb = await asyncio.to_thread(embed_fn, fact_content)
            except Exception as e:
                logger.warning("Memory extraction embed failed: %s", e)

        importance = _IMPORTANCE_BY_CATEGORY.get(category, 0.3)

        # Store via store_classified (with cosine dedup)
        write_scope = "agent" if agent_id else "global"
        try:
            inserted = await ltm.store_classified(
                fact_content,
                importance,
                emb,
                category,
                tags,
                slot_hint,
                scope=write_scope,
                agent_id=agent_id,
            )
            logger.info(
                "Memory extracted: %s = %s (category=%s, importance=%.2f, inserted=%s)",
                k, v, category, importance, inserted,
            )
        except Exception as e:
            logger.warning("Memory extraction store failed: %s", e)


# ── User-side preference extraction (LLM with rule fallback) ────────────────


_PREFERENCE_EXTRACTION_PROMPT = (
    "你是一个用户画像提取助手。只提取【用户本人】明确表达的、稳定的个人偏好或身份。\n"
    "严格规则：\n"
    "1. 只有用户明确说出自己的名字（如「我叫X」「我的名字是X」）才提取 姓名；"
    "用户喜欢/提到的明星、作品、人物（如「我喜欢周润发」）绝不能当作用户的姓名。\n"
    "2. 只提取关于用户自身的长期偏好（喜好、口味、习惯、风格、职业等）；"
    "用户临时询问或提到的对象不是偏好（如问「北京天气」不代表用户偏好地点是北京）。\n"
    "3. 用户明确陈述自己所在城市/地区（如「我在重庆」「我目前在重庆」「我住在上海」）"
    "应提取为 所在地；但用户仅查询某地信息（如「北京天气」「东京有什么好玩」）不算。\n"
    "4. key 用稳定简洁的类别名（姓名/喜好/口味/风格/职业/所在地 等），同一类信息始终用同一个 key，不要造近义新键。\n"
    "输出 JSON 对象（key=类别名，value=具体值），没有则输出 {}。只输出 JSON，不要有其他内容。"
)


_PREFERENCE_MERGE_PROMPT = (
    "你是一个偏好合并助手。下面是用户的所有偏好键值对（JSON 对象）。\n"
    "请检查并合并语义重复的 key-value，只保留更具体、更完整的值。\n"
    "合并规则：\n"
    "1. 只合并语义明确相同的条目（如「喜好」和「喜欢」指向同一含义时合并为「喜好」）。\n"
    "2. 合并后保留更具体、更完整的 value。\n"
    "3. 语义不同的条目保持不变。\n"
    "输出合并后的 JSON 对象，不要有其他内容。\n"
)


def _truncate_value(value: str) -> str:
    """Truncate a preference value at the first sentence separator.

    Location and preference values should be concise (e.g. '重庆', not
    '重庆，看看有什么好玩1的'). Splits on Chinese/English commas, periods,
    exclamation marks, and question marks.
    """
    for sep in ("，", "。", "！", "？", ",", ".", "!", "?"):
        idx = value.find(sep)
        if idx >= 0:
            value = value[:idx]
    return value.strip()


def _extract_rule_based(msg: str) -> Dict[str, str]:
    """Rule-based preference fallback. Ports ``Preference.extract_and_save_sync``
    rules ("我喜欢" / "我爱" / "我叫" / "我在" / "我住在"), returning the first
    match as a single-entry dict — faithful to the original, which returns on
    the first hit.

    For location rules ("我在" family), the full marker is used as the
    separator to avoid splitting inside the marker, and the value is
    truncated at the first sentence separator to avoid capturing the
    entire sentence (e.g. "重庆" not "重庆，看看有什么好玩1的").
    """
    if not msg:
        return {}
    # For location rules, use the full marker as separator to avoid
    # splitting inside the marker (e.g. "我现在在" contains "在").
    rules = [
        ("我喜欢", "喜欢", "喜好", False),
        ("我爱", "爱", "喜好", False),
        ("我叫", "叫", "姓名", False),
        ("我目前在", "我目前在", "所在地", True),
        ("我现在在", "我现在在", "所在地", True),
        ("我住在", "我住在", "所在地", True),
        ("我位于", "我位于", "所在地", True),
        ("我在", "我在", "所在地", True),
    ]
    for marker, sep, key, truncate in rules:
        if marker not in msg:
            continue
        parts = msg.split(sep, 1)
        if len(parts) < 2:
            continue
        value = parts[1].strip()
        if not value:
            continue
        if truncate:
            value = _truncate_value(value)
            if not value:
                continue
        return {key: value}
    return {}


async def extract_preferences(
    generate_fn: Optional[Callable],
    msg: str,
    existing_keys: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Extract user preferences from a user message via LLM, with rule fallback.

    When ``generate_fn`` is None, the LLM call raises, or the LLM returns
    non-JSON / an empty object, falls back to ``_extract_rule_based(msg)`` so
    preference extraction degrades gracefully without throwing.

    Args:
        generate_fn: LLM generate function (system_prompt, user_msg) -> str
        msg: The user message text
        existing_keys: Optional list of current preference keys. When provided,
            the LLM prompt includes the key list and instructs the model to
            reuse semantically equivalent existing keys rather than creating
            synonyms.
    """
    if not generate_fn or not msg:
        return _extract_rule_based(msg)

    # Build dynamic prompt: append existing_keys rule when provided
    system_prompt = _PREFERENCE_EXTRACTION_PROMPT
    if existing_keys:
        keys_str = ", ".join(existing_keys)
        system_prompt = (
            system_prompt
            + f"4. 已有的偏好 key：{keys_str}。"
            "如果新提取的偏好与已有 key 语义相同，必须复用已有 key。\n"
        )

    try:
        raw = await asyncio.to_thread(generate_fn, system_prompt, msg)
    except Exception as e:
        logger.warning("Preference LLM extraction failed: %s", e)
        return _extract_rule_based(msg)

    raw = _strip_code_fence(raw)
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.debug("Preference extraction: LLM output is not valid JSON: %s", raw[:100])
        return _extract_rule_based(msg)

    if not isinstance(parsed, dict) or not parsed:
        return _extract_rule_based(msg)

    return {
        str(k): str(v) for k, v in parsed.items() if k and v not in (None, "")
    }
