"""manage_skills tool — list / create / delete skills.

Reuses skill_service for filesystem operations. Skills are global (not per-user),
so there is no user_id scoping.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.manage_base import emit_guide_side_effect

# ── Handler + sub-handlers ───────────────────────────────────────────────────


async def _manage_skills_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    action = args.get("action", "")

    if action == "list":
        return _list_skills()
    elif action == "create":
        return _create_skill(args, ctx)
    elif action == "delete":
        return _delete_skill(args, ctx)
    else:
        return err(f"Unknown action: {action}")


def _list_skills() -> ToolResult:
    from app.services.skill_service import list_skills

    skills = list_skills()
    return ok({
        "skills": [
            {
                "slug": s.slug,
                "name": s.name,
                "description": s.description,
                "triggerKeywords": s.trigger_keywords,
            }
            for s in skills
        ]
    })


def _create_skill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.services.skill_service import SkillError, save_skill

    name = args.get("name", "").strip()
    description = args.get("description", "").strip()
    content = args.get("content", "")

    if not name:
        return err("name is required for create action")
    if not description:
        return err("description is required for create action")

    frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n\n"
    files = {"SKILL.md": frontmatter + content}

    try:
        meta = save_skill(files)
    except SkillError as e:
        return err(str(e))

    emit_guide_side_effect(ctx=ctx, target="skills", action="create")
    return ok({
        "slug": meta.slug,
        "name": meta.name,
        "description": meta.description,
        "message": f"已创建 Skill「{meta.name}」",
    })


def _delete_skill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if not args.get("confirm", False):
        return err("删除操作需要先通过 ask_user 向用户确认，并传 confirm=true")

    slug = args.get("slug")
    if not slug:
        return err("slug is required for delete action")

    from app.services.skill_service import delete_skill, list_skills

    existing = {s.slug for s in list_skills()}
    if slug not in existing:
        return err(f"Skill not found: {slug}")

    delete_skill(slug)
    emit_guide_side_effect(ctx=ctx, target="skills", action="delete")
    return ok({"message": f"已删除 Skill「{slug}」"})


# ── Tool definition ──────────────────────────────────────────────────────────

manage_skills_tool = ToolDef(
    name="manage_skills",
    description=(
        "管理 Skill：列表 / 创建 / 删除。"
        "action: list | create | delete。"
        "delete 操作需要先通过 ask_user 向用户确认，并传 confirm=true。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "delete"],
            },
            "name": {"type": "string"},
            "description": {"type": "string"},
            "content": {"type": "string"},
            "slug": {"type": "string"},
            "confirm": {"type": "boolean", "default": False},
        },
        "required": ["action"],
    },
    handler=_manage_skills_handler,  # type: ignore[assignment]
)
