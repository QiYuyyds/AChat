"""skill_auto_activator hook — suggests skill loading based on tool usage and user messages.

Registers two handlers that share a rule table auto-derived from skill
frontmatter ``trigger_keywords`` fields:

1. ``post_tool_use`` — inspects tool name and arguments (fs_write path suffix,
   bash command substring), matches against keywords.
2. ``on_run_start`` — extracts the last user message, matches keywords as
   case-insensitive substrings.

Both handlers check that the suggested slug exists in ``list_skills()`` AND
that ``load_skill`` is in the current run's ``tool_names`` before injecting.

Default disabled — agents must include "skill_auto_activator" in their
hook_names configuration to enable it.
"""

from __future__ import annotations

import logging

from app.services.hook_registry import HookContext, HookEvent, HookRegistry, HookResult

logger = logging.getLogger(__name__)

# Rule table: {slug: [keyword1, keyword2, ...]}, auto-derived from skill frontmatter.
_RULE_TABLE: dict[str, list[str]] = {}


def _build_rule_table() -> dict[str, list[str]]:
    """Build rule table from registered skills' trigger_keywords.

    Calls ``list_skills()`` and maps each skill with non-empty trigger_keywords
    to its keyword list. Skills without trigger_keywords are excluded.
    """
    from app.services.skill_service import list_skills

    table: dict[str, list[str]] = {}
    for meta in list_skills():
        if meta.trigger_keywords:
            table[meta.slug] = meta.trigger_keywords
    return table


def _match_tool_against_keywords(tool_name: str, args: dict, keywords: list[str]) -> bool:
    """Check if a tool call matches any keyword (case-insensitive).

    - ``fs_write``: checks path suffix against each keyword
    - ``bash``: checks command substring against each keyword
    - Other tools: no match
    """
    lowered = [kw.lower() for kw in keywords]
    if tool_name == "fs_write":
        path = str(args.get("path", "")).lower()
        return any(path.endswith(kw) for kw in lowered)
    if tool_name == "bash":
        command = str(args.get("command", "")).lower()
        return any(kw in command for kw in lowered)
    return False


def _check_skill_available(slug: str, ctx: HookContext) -> bool:
    """Check that slug exists in registry AND load_skill is in tool_names."""
    # Check load_skill availability
    tool_names = ctx.tool_names
    if not tool_names or "load_skill" not in tool_names:
        return False

    # Check skill existence
    from app.services.skill_service import list_skills

    known_slugs = {m.slug for m in list_skills()}
    return slug in known_slugs


def _build_hint(slug: str) -> str:
    return (
        f"检测到相关操作，可调用 load_skill('{slug}') "
        f"获取相关最佳实践指导。"
    )


async def _post_tool_use(ctx: HookContext) -> HookResult | None:
    if ctx.tool_name is None:
        return None

    args = ctx.args if isinstance(ctx.args, dict) else {}

    # Sort slugs for deterministic ordering when multiple skills match
    for slug in sorted(_RULE_TABLE.keys()):
        keywords = _RULE_TABLE[slug]
        if not _match_tool_against_keywords(ctx.tool_name, args, keywords):
            continue
        if not _check_skill_available(slug, ctx):
            logger.debug(
                "[skill_auto_activator] skill '%s' not available (missing or load_skill not in tools)",
                slug,
            )
            continue
        hint = _build_hint(slug)
        logger.info(
            "[skill_auto_activator] post_tool_use matched: tool=%s slug=%s",
            ctx.tool_name, slug,
        )
        return HookResult(
            action="inject",
            data=[{"type": "system_hint", "content": hint}],
        )

    return HookResult(action="allow")


async def _on_run_start(ctx: HookContext) -> HookResult | None:
    messages = ctx.messages
    if not messages:
        return HookResult(action="allow")

    # Extract last user message text
    user_text = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                user_text = content.lower()
            elif isinstance(content, list):
                # Multimodal: concatenate text parts
                user_text = " ".join(
                    p.get("text", "") if isinstance(p, dict) and p.get("type") == "text"
                    else "" if isinstance(p, dict)
                    else str(p)
                    for p in content
                ).lower()
            break

    if not user_text:
        return HookResult(action="allow")

    # Sort slugs for deterministic ordering
    for slug in sorted(_RULE_TABLE.keys()):
        keywords = _RULE_TABLE[slug]
        if not any(kw.lower() in user_text for kw in keywords):
            continue
        if not _check_skill_available(slug, ctx):
            logger.debug(
                "[skill_auto_activator] on_run_start skill '%s' not available",
                slug,
            )
            continue
        hint = _build_hint(slug)
        logger.info(
            "[skill_auto_activator] on_run_start matched: slug=%s",
            slug,
        )
        return HookResult(
            action="inject",
            data=[{"type": "system_hint", "content": hint}],
        )

    return HookResult(action="allow")


def register(registry: HookRegistry) -> None:
    global _RULE_TABLE
    _RULE_TABLE = _build_rule_table()
    registry.register(
        HookEvent.POST_TOOL_USE, _post_tool_use, priority=10, name="skill_auto_activator"
    )
    registry.register(
        HookEvent.ON_RUN_START, _on_run_start, priority=10, name="skill_auto_activator"
    )
