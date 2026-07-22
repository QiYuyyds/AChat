"""manage_profile tool — get / update user profile and settings.

Reuses api/profile.py logic + settings_service for API key management.
All operations are scoped by ToolContext.user_id. Modifying API keys requires
confirm=true.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.manage_base import emit_guide_side_effect

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mask_key(key: str | None) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


# ── Handler + sub-handlers ───────────────────────────────────────────────────


async def _manage_profile_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    action = args.get("action", "")
    user_id = ctx.user_id
    if user_id is None:
        return err("manage_profile requires a user context")

    target = args.get("target", "profile")

    if action == "get":
        return await _get_profile(user_id, target)
    elif action == "update":
        return await _update_profile(args, user_id, target, ctx)
    else:
        return err(f"Unknown action: {action}")


async def _get_profile(user_id: str, target: str) -> ToolResult:
    if target == "profile":
        return await _get_profile_data(user_id)
    else:
        return await _get_settings_data(user_id)


async def _get_profile_data(user_id: str) -> ToolResult:
    from app.api.profile import _read_profile_prefs
    from app.db.engine import get_remote_db
    from app.db.models import User

    async with get_remote_db() as db:
        user = await db.get(User, user_id)
        if user is None:
            return err("User not found")

    prefs = await _read_profile_prefs(user_id)
    return ok({
        "name": prefs.get("姓名", ""),
        "location": prefs.get("所在地", ""),
        "hometown": prefs.get("家乡", ""),
        "preferences": prefs.get("喜好", ""),
        "bio": prefs.get("简介", ""),
        "avatarUrl": user.avatar_url,
    })


async def _get_settings_data(user_id: str) -> ToolResult:
    from app.services.settings_service import get_user_settings

    settings = await get_user_settings(user_id)
    return ok({
        "anthropicApiKey": _mask_key(settings.anthropic_api_key),
        "openaiApiKey": _mask_key(settings.openai_api_key),
        "deepseekApiKey": _mask_key(settings.deepseek_api_key),
        "arkApiKey": _mask_key(settings.ark_api_key),
        "anthropicBaseUrl": settings.anthropic_base_url,
        "companionMode": settings.companion_mode,
        "obsidianVaultPath": settings.obsidian_vault_path,
    })


async def _update_profile(
    args: dict[str, Any], user_id: str, target: str, ctx: ToolContext
) -> ToolResult:
    if target == "profile":
        return await _update_profile_data(args, user_id, ctx)
    else:
        return await _update_settings_data(args, user_id, ctx)


async def _update_profile_data(
    args: dict[str, Any], user_id: str, ctx: ToolContext
) -> ToolResult:
    from app.memory.preference import Preference

    pref = Preference(user_id=user_id)
    profile_keys = {
        "display_name": "姓名",
        "location": "所在地",
        "hometown": "家乡",
        "preferences": "喜好",
        "bio": "简介",
    }

    for field, canonical_key in profile_keys.items():
        if field in args:
            value = args[field]
            if value is None:
                await pref.delete(canonical_key)
            else:
                await pref.set(canonical_key, str(value), source="manual")

    # Update user name in users table if display_name is provided
    if "display_name" in args and args["display_name"]:
        from app.db.engine import get_remote_db
        from app.db.models import User

        async with get_remote_db() as db:
            user = await db.get(User, user_id)
            if user is not None:
                user.name = str(args["display_name"])

    from app.infra.cache_helpers import invalidate_user_preferences_cache
    await invalidate_user_preferences_cache(user_id)
    emit_guide_side_effect(ctx=ctx, target="profile", action="update")
    return ok({"message": "已更新用户画像"})


async def _update_settings_data(
    args: dict[str, Any], user_id: str, ctx: ToolContext
) -> ToolResult:
    settings_patch = args.get("settings", {})
    if not settings_patch:
        return err("settings is required for update with target=settings")

    # Check for API key changes — require confirm
    api_key_fields = {"anthropicApiKey", "openaiApiKey", "deepseekApiKey", "arkApiKey"}
    has_api_key_change = any(k in settings_patch for k in api_key_fields)
    if has_api_key_change and not args.get("confirm", False):
        return err("修改 API Key 需要先通过 ask_user 向用户确认，并传 confirm=true")

    from app.services.settings_service import UserSettingsPatch, update_user_settings

    patch = UserSettingsPatch(
        anthropic_api_key=settings_patch.get("anthropicApiKey"),
        openai_api_key=settings_patch.get("openaiApiKey"),
        deepseek_api_key=settings_patch.get("deepseekApiKey"),
        ark_api_key=settings_patch.get("arkApiKey"),
        anthropic_base_url=settings_patch.get("anthropicBaseUrl"),
        obsidian_vault_path=settings_patch.get("obsidianVaultPath"),
    )

    try:
        await update_user_settings(user_id, patch)
    except Exception as e:
        return err(f"Failed to update settings: {e}")

    emit_guide_side_effect(ctx=ctx, target="profile", action="update")
    return ok({"message": "已更新全局设置"})


# ── Tool definition ──────────────────────────────────────────────────────────

manage_profile_tool = ToolDef(
    name="manage_profile",
    description=(
        "管理用户画像和全局设置。"
        "action: get | update。"
        "target: profile | settings。"
        "修改 API Key 需要先通过 ask_user 向用户确认，并传 confirm=true。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "update"],
            },
            "target": {
                "type": "string",
                "enum": ["profile", "settings"],
                "default": "profile",
            },
            "display_name": {"type": "string"},
            "location": {"type": "string"},
            "hometown": {"type": "string"},
            "preferences": {"type": "string"},
            "bio": {"type": "string"},
            "settings": {"type": "object"},
            "confirm": {"type": "boolean", "default": False},
        },
        "required": ["action"],
    },
    handler=_manage_profile_handler,  # type: ignore[assignment]
)
