"""Preference extraction utilities (preserved from old memory_writer.py).

The LTM extraction functions have been removed (replaced by auto_memory + auto_dream).
Only preference-related functions are kept here because the Preference system
(PG KV table + manage_profile tool + ProfileSource injection) is preserved.

Exports:
  - classify_memory_content: Rule-based classification for ProfileSource scoring
  - _IMPORTANCE_BY_CATEGORY: Importance floor per category
  - extract_preferences: LLM-based preference extraction with rule fallback
  - _PREFERENCE_MERGE_PROMPT: LLM prompt for preference consolidation
  - _strip_code_fence: Helper to strip markdown code fences from LLM output
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)

_IMPORTANCE_BY_CATEGORY: dict[str, float] = {
    "identity": 0.9,
    "policy": 0.8,
    "preference": 0.7,
    "fact": 0.5,
    "episodic": 0.4,
    "tool_failure": 0.3,
    "general": 0.3,
}


def _strip_code_fence(raw: str) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"^```json", "", raw)
    raw = re.sub(r"^```", "", raw)
    raw = re.sub(r"```$", "", raw)
    return raw.strip()


def _contains_any(s: str, *subs: str) -> bool:
    return any(sub in s for sub in subs)


def classify_memory_content(key: str, value: str) -> tuple[str, list[str], str]:
    """Classify memory content using rules. Returns (category, tags, slot_hint)."""
    combined = f"{key}{value}"
    if _contains_any(combined, "叫", "名字", "姓名", "是我", "我是"):
        return "identity", ["name"], "profile"
    if _contains_any(combined, "所在地", "位于", "住在", "在城市", "在重庆", "在北京", "在上海", "在广州", "在深圳", "在成都"):
        return "identity", ["location"], "profile"
    if _contains_any(combined, "家乡", "老家"):
        return "identity", ["hometown"], "profile"
    if _contains_any(combined, "喜欢", "偏好", "习惯", "爱好", "讨厌", "不喜欢", "prefer"):
        return "preference", ["preference"], "profile"
    if _contains_any(combined, "工具", "失败", "错误", "报错", "异常", "error", "bug"):
        return "tool_failure", ["tool", "error"], "tool_state"
    if _contains_any(combined, "禁止", "不要", "不能", "必须", "强制", "must", "never"):
        return "policy", ["constraint"], "constraints"
    return "", [], ""


_PREFERENCE_MERGE_PROMPT = (
    "你是一个偏好合并助手。下面是用户的所有偏好键值对（JSON 对象）。\n"
    "请检查并合并语义重复的 key-value，只保留更具体、更完整的值。\n"
    "合并规则：\n"
    "1. 只合并语义明确相同的条目（如「喜好」和「喜欢」指向同一含义时合并为「喜好」）。\n"
    "2. 合并后保留更具体、更完整的 value。\n"
    "3. 语义不同的条目保持不变。\n"
    "输出合并后的 JSON 对象，不要有其他内容。\n"
)


_PREFERENCE_EXTRACTION_PROMPT = """\
You are a Personal Information Organizer, specialized in accurately storing user facts, memories, and preferences.

Your primary role is to extract relevant pieces of information from the USER'S messages and organize them as key-value pairs.

# [IMPORTANT]: GENERATE FACTS SOLELY BASED ON THE USER'S MESSAGES. DO NOT INCLUDE INFORMATION FROM ASSISTANT OR SYSTEM MESSAGES.
# [IMPORTANT]: YOU WILL BE PENALIZED IF YOU INCLUDE INFORMATION FROM ASSISTANT OR SYSTEM MESSAGES.

## Types of Information to Extract

Focus on the following types of information when present in user messages:

1. Personal Preferences: Likes, dislikes, tastes in food, music, movies, activities, etc.
2. Personal Details: Name, age, location, hometown, relationships, education, etc.
3. Plans and Intentions: Future plans, goals, projects, things the user intends to do.
4. Activity Preferences: Hobbies, sports, travel preferences, weekend activities.
5. Health and Wellness: Dietary restrictions, fitness routines, health conditions.
6. Professional Details: Job title, company, skills, technologies used, career goals.
7. Miscellaneous Information: Any other interesting or unique details about the user.

## Rules

1. Only extract information the USER explicitly states about themselves.
2. Do not extract information from questions the user asks (e.g., "What's the weather in Beijing?" does not mean the user is in Beijing).
3. User explicitly stating their name (e.g., "My name is X", "I am X") should extract as name; mentioning celebrities or characters does NOT count as the user's name.
4. Use stable, concise category names as keys (e.g., 姓名/职业/所在地/喜好/技术栈). Always use the same key for the same type of information. Do not create synonym keys.
5. Values should be concise but complete (e.g., "前端工程师" not "我是一个前端工程师，主要负责...").

## Output Format

Output a JSON object where keys are category names and values are the extracted information.
If nothing relevant is found, output an empty object: {}

## Examples

User: Hi, my name is John. I am a software engineer.
Output: {"姓名": "John", "职业": "软件工程师"}

User: 我叫张三，我是前端工程师，用 TypeScript。
Output: {"姓名": "张三", "职业": "前端工程师", "技术栈": "TypeScript"}

Return only the JSON object, no other text.
"""


def _truncate_value(value: str) -> str:
    for sep in ("，", "。", "！", "？", ",", ".", "!", "?"):
        idx = value.find(sep)
        if idx >= 0:
            value = value[:idx]
    return value.strip()


def _extract_rule_based(msg: str) -> dict[str, str]:
    if not msg:
        return {}
    rules = [
        ("我喜欢", "喜欢", "喜好", False),
        ("我爱", "爱", "喜好", False),
        ("我叫", "叫", "姓名", False),
        ("我家乡在", "我家乡在", "家乡", True),
        ("我老家在", "我老家在", "家乡", True),
        ("老家是", "老家是", "家乡", True),
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


async def extract_preferences_compat(
    generate_fn: Callable | None,
    msg: str,
    existing_keys: list[str] | None = None,
) -> dict[str, str]:
    """Extract user preferences from a user message via LLM, with rule fallback."""
    if not generate_fn or not msg:
        return _extract_rule_based(msg)

    system_prompt = _PREFERENCE_EXTRACTION_PROMPT
    if existing_keys:
        keys_str = ", ".join(existing_keys)
        system_prompt = (
            system_prompt
            + f"\n\n## Existing Keys\n"
            f"The following keys already exist: {keys_str}. "
            "If new preferences overlap with existing keys semantically, reuse the existing key name."
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
