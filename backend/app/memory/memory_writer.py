"""Memory writer — LLM-based memory extraction from conversations.

Adapted from mem0 V3 (ADDITIVE_EXTRACTION_PROMPT) for LTM extraction
and mem0 V2 (USER_MEMORY_EXTRACTION_PROMPT) for Preference extraction.

Key functions:
  - ``classify_memory_content(key, value)``: Rule-based classification
    (identity/preference/tool_failure/policy). Used by ProfileSource for
    preference scoring/sorting.
  - ``extract_ltm_memories(generate_fn, embed_fn, ltm, user_msg, assistant_msg)``:
    Async extraction of natural language memories from full conversation,
    stored directly to LTM without classification routing.
  - ``extract_preferences(generate_fn, msg, existing_keys)``:
    Async extraction of user preferences (KV) from user messages.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Protocol

logger = logging.getLogger(__name__)


# Importance floor per memory category. Used by ProfileSource for
# preference scoring/sorting.
_IMPORTANCE_BY_CATEGORY: dict[str, float] = {
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

    async def set(self, key: str, value: str, source: str = "extracted") -> None: ...


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


def classify_memory_content(key: str, value: str) -> tuple[str, list[str], str]:
    """Classify memory content using rules.

    Returns (category, tags, slot_hint).
    Returns empty strings if no rule matches.

    Used by ProfileSource in prompt_assembler.py for preference scoring/sorting.
    """
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


# ── LTM extraction prompt (adapted from mem0 V3 ADDITIVE_EXTRACTION_PROMPT) ──

_LTM_EXTRACTION_SYSTEM_PROMPT = """\
# ROLE

You are a Memory Extractor — a precise, evidence-bound processor responsible for extracting rich, contextual memories from conversations. Your sole operation is ADD: identify every piece of memorable information and produce self-contained, contextually rich factual statements.

You extract from BOTH user and assistant messages. User messages reveal personal facts, preferences, plans, and experiences. Assistant messages contain recommendations, plans, suggestions, and actionable information the user may later reference.

# GUIDELINES

## What to Extract

Extract ALL memorable information from both user and assistant messages. Think broadly:

**From user messages:**
- Personal details, preferences, plans, relationships, professional information
- Opinions, subjective experiences, emotional states, motivations
- Activity preferences, health and wellness information
- Plans, intentions, goals, decisions
- Miscellaneous information worth remembering

**From assistant messages:**
- Recommendations, suggestions, plans, actionable information
- Technical details, project context, decisions made collaboratively
- Information the user may want to reference later

## What NOT to Extract
- Greetings, filler, conversation mechanics ("hello", "ok", "sure", "sounds good")
- Transient context (e.g., "let me think about that") unless it reveals a preference or plan
- Information already captured by another memory in the same response (avoid duplication)

## Memory Quality Standards

### Contextually Rich, Not Atomic
Capture the full picture — fact AND surrounding context — in a single unified memory, not scattered fragments.
Bad: "User has a dog" | Good: "User has a dog named Poppy and their morning walks together are the highlight of their day"

This applies especially to **transitions and changes**. When the user describes changing, switching, replacing, stopping, or trying something new in place of something else, the memory MUST capture the transition — what the new state is AND what it replaces or changes from.

### Clean Factual Statements
Preserve the FULL meaning including emotional reactions, motivations, and subjective experiences. Remove filler words and conversation mechanics, but KEEP:
- Emotional states: "scared but reassured", "happy and thankful"
- Motivations and reasons: "motivated by her own journey"
- Subjective descriptions: "resilient", "therapeutic"

### Self-Contained
Every memory must be understandable on its own. Replace all pronouns with specific names or "User."

### Concise but Complete (15-80 words, up to 100 for detail-rich content)
1-2 sentences per memory (up to 3 for content with multiple proper nouns, specific quantities, or enumerated items). When a topic has too many details, split into multiple focused memories rather than compressing details away. NEVER sacrifice a proper noun, title, date, or specific detail to meet a word count.

### Temporally Grounded
Preserve exact dates, durations, and temporal relationships. Convert relative to absolute using context when available. "18 days" stays "18 days", not "some time."

### Numerically Precise
Preserve exact quantities as stated. "416 pages" stays "416 pages", not "about 400 pages."

## Deduplication Within Response
Each piece of information MUST appear exactly once in the output. If the user and assistant both mention the same fact, extract it once from the original source (typically the user).

## Attribution
For each memory, set "attributed_to" to "user" or "assistant" based on who originally provided the information.

## Language Requirement
CRITICAL: Respond in the SAME LANGUAGE and SCRIPT as the input messages.
1. Match the language of the user's messages exactly — if they write in Chinese, extract in Chinese; Japanese in Japanese; etc.
2. Preserve the exact script/alphabet of the input.
3. Do NOT translate or transliterate into English unless the input is already in English.
4. Technical terms, proper nouns, and brand names should be preserved in their original form as used in the input.
5. If the input mixes languages, preserve both the mixed language style AND the script.

# OUTPUT FORMAT

Return a JSON object with a "memory" array. Each entry has "id", "text", "attributed_to", "summary", and "keywords":

{"memory": [
  {"id": "0", "text": "...", "attributed_to": "user", "summary": "...", "keywords": ["..."]},
  {"id": "1", "text": "...", "attributed_to": "assistant", "summary": "...", "keywords": ["..."]}
]}

If nothing memorable was said, return: {"memory": []}

## Summary Rules

- "summary" is a 3-10 character Chinese or 3-10 word English self-contained title.
- It MUST be understandable without the full content.
- It captures the memory's core topic as a concise title.
- Do NOT use generic words like "部署" or "配置" — be specific (e.g., "部署端口冲突" not "部署").
- Example: content="用户喜欢TypeScript，偏好React框架" → summary="用户前端技术栈"

## Keywords Rules

- "keywords" is a list of 3-5 retrieval keywords per memory.
- Prefer proper nouns, core concepts, and technical terms (e.g., "TypeScript", "React", "PostgreSQL").
- Do NOT include generic words like "user", "project", "system", "memory".
- Keywords are used for keyword-based retrieval matching, so choose terms that uniquely identify this memory.
- Example: content="项目用 Next.js 16" → keywords=["Next.js", "Next16", "前端框架"]

# EXAMPLES

## Example 1: Multi-Topic Conversation

New Messages:
[{"role": "user", "content": "I'm John, a software engineer at Google. I love hiking on weekends and I'm planning to learn Rust next month."}]

Output:
{"memory": [
  {"id": "0", "text": "User's name is John", "attributed_to": "user", "summary": "用户姓名", "keywords": ["John", "姓名", "identity"]},
  {"id": "1", "text": "User is a software engineer at Google", "attributed_to": "user", "summary": "用户职业", "keywords": ["Google", "software engineer", "职业"]},
  {"id": "2", "text": "User loves hiking on weekends", "attributed_to": "user", "summary": "用户爱好", "keywords": ["hiking", "周末", "爱好"]},
  {"id": "3", "text": "User is planning to learn Rust next month", "attributed_to": "user", "summary": "学习计划", "keywords": ["Rust", "学习计划", "编程"]}
]}

Four dimensions: (1) name, (2) professional, (3) hobby, (4) plan. Each extracted separately with summary and keywords.

## Example 2: Nothing to Extract

New Messages:
[{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello! How can I help?"}]

Output:
{"memory": []}

No memorable information — greetings only.

## Example 3: Document / Reference Material — Extract Content, Not Actions

New Messages:
[{"role": "user", "content": "Remember this: The project uses PostgreSQL 16 with Redis for caching. The API is built with FastAPI and the frontend uses Next.js 16."}, {"role": "assistant", "content": "Got it, noted."}]

Output:
{"memory": [
  {"id": "0", "text": "The project uses PostgreSQL 16 with Redis for caching", "attributed_to": "user", "summary": "项目数据库", "keywords": ["PostgreSQL", "Redis", "缓存"]},
  {"id": "1", "text": "The API is built with FastAPI and the frontend uses Next.js 16", "attributed_to": "user", "summary": "项目技术栈", "keywords": ["FastAPI", "Next.js", "前端"]}
]}

Extract the factual content, not the acknowledgment.

## Example 4: Exhaustive Checklist — Verify Before Outputting

Before finalizing, verify:
1. Did you capture ALL memorable information from both user and assistant?
2. Is each memory self-contained (no pronouns without antecedents)?
3. Are transitions/changes captured with both old and new state?
4. Are exact numbers, dates, and proper nouns preserved?
5. Is each piece of information extracted exactly once (no duplicates)?
6. Is each memory attributed to the correct source?
7. Is the language matched to the input language?
"""


# ── Async extraction from full conversation ─────────────────────────────────


async def extract_ltm_memories(
    generate_fn: Callable,
    embed_fn: Callable | None,
    ltm,
    user_msg: str,
    assistant_msg: str,
    *,
    agent_id: str = "",
    user_id: str | None = None,
    existing_keys: list[str] | None = None,
) -> None:
    """Extract natural language memories from a full conversation turn.

    Uses the V3-adapted extraction prompt to process both user and assistant
    messages in a single LLM call, producing self-contained memory strings
    that are stored directly to LTM via ``store_classified()`` without
    classification routing.

    Args:
        generate_fn: LLM generate function (system_prompt, user_msg) -> str
        embed_fn: Optional embedding function (text) -> list[float]
        ltm: LongTerm memory instance with ``store_classified()`` method
        user_msg: The user message text
        assistant_msg: The assistant reply text
        agent_id: When non-empty, extracted memories are written with
            ``scope='agent'`` and ``agent_id`` so they are isolated per-agent.
        user_id: User ID for multi-user isolation.
        existing_keys: Optional list of current preference keys. When provided,
            the LLM prompt includes the key list and instructs the model to
            reuse semantically equivalent existing keys.
    """
    if not generate_fn or (not user_msg and not assistant_msg):
        return

    # Build dynamic prompt with existing keys (fixes the dead-code bug where
    # the original constant was passed instead of the modified variable)
    system_prompt = _LTM_EXTRACTION_SYSTEM_PROMPT
    if existing_keys:
        keys_str = ", ".join(existing_keys)
        system_prompt = (
            system_prompt
            + f"\n\n## Existing Preference Keys\n"
            f"The following preference keys already exist: {keys_str}. "
            "If extracted memories overlap with these preferences, use the same key naming convention."
        )

    # Build V3-style "## New Messages" section
    messages = []
    if user_msg:
        messages.append({"role": "user", "content": user_msg})
    if assistant_msg:
        messages.append({"role": "assistant", "content": assistant_msg})

    prompt = "## New Messages\n"
    prompt += json.dumps(messages, ensure_ascii=False)

    try:
        raw = await asyncio.to_thread(generate_fn, system_prompt, prompt)
    except Exception as e:
        logger.warning("LTM memory extraction LLM call failed: %s", e)
        return

    raw = _strip_code_fence(raw)
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.debug("LTM memory extraction: LLM output is not valid JSON: %s", raw[:200])
        return

    if not isinstance(parsed, dict):
        return

    memories = parsed.get("memory", [])
    if not isinstance(memories, list) or not memories:
        return

    write_scope = "agent" if agent_id else "global"
    for mem in memories:
        if not isinstance(mem, dict):
            continue
        text = str(mem.get("text", "")).strip()
        if not text:
            continue

        attributed_to = str(mem.get("attributed_to", "user")).strip() or "user"

        # Parse structured fields (summary + keywords)
        summary = str(mem.get("summary", "")).strip()
        keywords_raw = mem.get("keywords", [])
        if isinstance(keywords_raw, list):
            keywords = [str(k).strip() for k in keywords_raw if str(k).strip()]
        else:
            keywords = []

        # Compute embedding from summary (concentrated semantic signal);
        # fall back to content for backward compatibility.
        embed_text = summary if summary else text
        emb = None
        if embed_fn:
            try:
                emb = await asyncio.to_thread(embed_fn, embed_text)
            except Exception as e:
                logger.warning("LTM memory extraction embed failed: %s", e)

        try:
            inserted = await ltm.store_classified(
                text,
                0.5,  # default importance; can be refined later
                emb,
                "",  # category: empty (no classification routing)
                [attributed_to],  # tags: source attribution
                "",  # slot_hint: empty
                scope=write_scope,
                agent_id=agent_id,
                user_id=user_id,
                summary=summary,
                keywords=keywords,
            )
            logger.info(
                "LTM memory extracted: %s (summary=%s, attributed_to=%s, inserted=%s)",
                text[:80], summary[:30], attributed_to, inserted,
            )
        except Exception as e:
            logger.warning("LTM memory extraction store failed: %s", e)


# ── Case memory extraction (task completion) ─────────────────────────────


_CASE_EXTRACTION_SYSTEM_PROMPT = """\
# ROLE

You are a Case Memory Extractor — an analyst that identifies reusable task experiences from completed conversations. Your goal is to distill actionable, cross-session knowledge: what approaches worked, what failed, and what patterns can be reused in similar future tasks.

# GUIDELINES

## What to Extract

Only extract memories with LONG-TERM LEARNING VALUE:
- Effective strategies or approaches that solved a problem (e.g., "先跑全量测试确认基线，再分步修改")
- Common pitfalls or anti-patterns discovered (e.g., "直接修改生产环境配置导致服务中断")
- Reusable patterns applicable to similar tasks (e.g., "重构认证模块时，先隔离接口层再改实现")
- Tool usage insights (e.g., "Milvus collection 需要先 load 再搜索")

## What NOT to Extract

- Trivial or obvious operations (e.g., "使用了 git commit")
- One-off details with no reusable value (e.g., "修改了文件第42行")
- Information already common knowledge
- Transient context only relevant to this specific session

## Extraction Quality Standards

Each case memory MUST:
1. Be self-contained and actionable
2. Describe the approach/pattern, not the specific instance
3. Have a clear summary (3-10 words) capturing the reusable insight
4. Include 3-5 keywords for retrieval (proper nouns, technical terms)

# OUTPUT FORMAT

Return a JSON object with a "cases" array. Each entry has "text", "summary", "keywords", and "outcome":

{"cases": [
  {"text": "...", "summary": "...", "keywords": ["..."], "outcome": "success|failure|insight"}
]}

If no reusable experience is found, return: {"cases": []}

# EXAMPLES

## Example 1: Successful refactoring

Session summary: 用户要求重构认证模块。先跑了全量测试确认基线，然后分步修改，每步跑测试。最后所有测试通过，成功合并。

Output:
{"cases": [
  {"text": "重构认证模块时，先跑全量测试确认基线，再分步修改每步验证，确保每步可回退", "summary": "认证模块重构策略", "keywords": ["重构", "认证", "测试", "分步修改"], "outcome": "success"}
]}

## Example 2: Trivial conversation

Session summary: 用户问了天气情况，然后聊了聊周末计划。

Output:
{"cases": []}

No reusable task experience found.

## Example 3: Tool failure insight

Session summary: 用户尝试用 Milvus 搜索但报错。发现 collection 需要先 load 才能搜索，加载后成功。

Output:
{"cases": [
  {"text": "Milvus collection 搜索前需要先调用 load 加载到内存，否则会报搜索错误", "summary": "Milvus搜索需先load", "keywords": ["Milvus", "collection", "load", "搜索"], "outcome": "insight"}
]}
"""


async def extract_case_memories(
    generate_fn: Callable,
    embed_fn: Callable | None,
    ltm,
    session_summary: str,
    *,
    task_result: str = "",
    agent_id: str = "",
    user_id: str | None = None,
) -> int:
    """Extract reusable case memories from a completed task/session.

    Uses the case extraction prompt to identify reusable task experiences
    from the session summary. Each extracted case is stored in LTM with
    ``category='case'``.

    Args:
        generate_fn: LLM generate function (system_prompt, user_msg) -> str
        embed_fn: Optional embedding function (text) -> list[float]
        ltm: LongTerm memory instance with ``store_classified()`` method
        session_summary: The SessionMemory summary text for the completed session
        task_result: Optional task outcome description (success/failure details)
        agent_id: When non-empty, case memories are written with scope='agent'
        user_id: User ID for multi-user isolation

    Returns:
        Number of case memories extracted and stored.
    """
    if not generate_fn or not session_summary:
        return 0

    # Build user message with session summary + optional task result
    user_msg = f"## Session Summary\n{session_summary}"
    if task_result:
        user_msg += f"\n## Task Result\n{task_result}"

    try:
        raw = await asyncio.to_thread(generate_fn, _CASE_EXTRACTION_SYSTEM_PROMPT, user_msg)
    except Exception as e:
        logger.warning("Case memory extraction LLM call failed: %s", e)
        return 0

    raw = _strip_code_fence(raw)
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.debug("Case memory extraction: LLM output is not valid JSON: %s", raw[:200])
        return 0

    if not isinstance(parsed, dict):
        return 0

    cases = parsed.get("cases", [])
    if not isinstance(cases, list) or not cases:
        return 0

    write_scope = "agent" if agent_id else "global"
    stored_count = 0
    for case in cases:
        if not isinstance(case, dict):
            continue
        text = str(case.get("text", "")).strip()
        if not text:
            continue

        summary = str(case.get("summary", "")).strip()
        keywords_raw = case.get("keywords", [])
        if isinstance(keywords_raw, list):
            keywords = [str(k).strip() for k in keywords_raw if str(k).strip()]
        else:
            keywords = []
        outcome = str(case.get("outcome", "insight")).strip() or "insight"

        # Compute embedding from summary (concentrated semantic signal)
        embed_text = summary if summary else text
        emb = None
        if embed_fn:
            try:
                emb = await asyncio.to_thread(embed_fn, embed_text)
            except Exception as e:
                logger.warning("Case memory extraction embed failed: %s", e)

        try:
            inserted = await ltm.store_classified(
                text,
                0.6,  # case memories have higher default importance
                emb,
                "case",  # category: case
                [outcome],  # tags: outcome type
                "",  # slot_hint: empty
                scope=write_scope,
                agent_id=agent_id,
                user_id=user_id,
                summary=summary,
                keywords=keywords,
            )
            if inserted:
                stored_count += 1
                logger.info(
                    "Case memory extracted: %s (summary=%s, outcome=%s)",
                    text[:80], summary[:30], outcome,
                )
        except Exception as e:
            logger.warning("Case memory extraction store failed: %s", e)

    return stored_count


# ── User-side preference extraction (LLM with rule fallback) ────────────────


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

User: Hi, I am looking for a restaurant in San Francisco.
Output: {}

User: Hi, my name is John. I am a software engineer.
Output: {"姓名": "John", "职业": "软件工程师"}

User: 我叫张三，我是前端工程师，用 TypeScript。
Output: {"姓名": "张三", "职业": "前端工程师", "技术栈": "TypeScript"}

User: I love hiking and I'm planning to learn Rust next month.
Output: {"爱好": "hiking", "计划": "下个月学习Rust"}

User: 我打算下周重构认证模块。
Output: {"计划": "下周重构认证模块"}

Return only the JSON object, no other text.
"""


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


def _extract_rule_based(msg: str) -> dict[str, str]:
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


async def extract_preferences(
    generate_fn: Callable | None,
    msg: str,
    existing_keys: list[str] | None = None,
) -> dict[str, str]:
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
