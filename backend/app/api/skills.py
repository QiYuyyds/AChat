"""Skills API — list / upload / delete agent skills (filesystem-backed).

Independent of the documents/RAG library. Upload accepts either a single
SKILL.md or a whole folder (webkitdirectory sends each file with its relative
path); both are rebased to the SKILL.md's directory and funnel through
skill_service.save_skill. Skill content lives on disk, never in the DB.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.services.skill_service import (
    SKILL_MD,
    SkillError,
    delete_skill,
    list_skills,
    save_skill,
)

router = APIRouter()


@router.get("/skills")
async def list_skills_route(user: User = Depends(get_current_user)) -> JSONResponse:
    skills = [
        {
            "slug": m.slug,
            "name": m.name,
            "description": m.description,
            "triggerKeywords": m.trigger_keywords,
        }
        for m in list_skills()
    ]
    return JSONResponse({"skills": skills})


@router.post("/skills/upload")
async def upload_skill(
    files: list[UploadFile],
    paths: Annotated[list[str] | None, Form()] = None,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Create a skill from an uploaded file or folder.

    ``paths[i]`` is the relative path of ``files[i]`` (folder upload sends
    webkitRelativePath; single-file upload may omit it, falling back to the
    filename). All paths are rebased to the directory containing SKILL.md.
    """
    if not files:
        return JSONResponse({"error": "No files uploaded"}, status_code=400)

    rel_paths = paths or []
    collected: dict[str, str] = {}
    for i, f in enumerate(files):
        rel = (rel_paths[i] if i < len(rel_paths) else None) or f.filename or f"file-{i}"
        rel = rel.replace("\\", "/").lstrip("/")
        raw = await f.read()
        try:
            collected[rel] = raw.decode("utf-8")
        except UnicodeDecodeError:
            return JSONResponse(
                {"error": f"Skill files must be UTF-8 text; '{rel}' is not"},
                status_code=400,
            )

    try:
        rebased = _rebase_to_skill_md(collected)
        meta = save_skill(rebased)
    except SkillError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    return JSONResponse(
        {
            "skill": {
                "slug": meta.slug,
                "name": meta.name,
                "description": meta.description,
                "triggerKeywords": meta.trigger_keywords,
            }
        },
        status_code=201,
    )


@router.delete("/skills/{slug}")
async def delete_skill_route(slug: str) -> JSONResponse:
    delete_skill(slug)
    return JSONResponse({"ok": True})


def _rebase_to_skill_md(files: dict[str, str]) -> dict[str, str]:
    """Make all paths relative to the directory holding the shallowest SKILL.md.

    Files outside that directory subtree are dropped. Raises SkillError if no
    SKILL.md is present.
    """
    md_paths = [p for p in files if p.split("/")[-1] == SKILL_MD]
    if not md_paths:
        raise SkillError("Upload must contain a SKILL.md")
    root_md = min(md_paths, key=lambda p: p.count("/"))
    prefix = root_md[: -len(SKILL_MD)]  # includes trailing slash, or "" at root

    out: dict[str, str] = {}
    for path, content in files.items():
        if prefix and not path.startswith(prefix):
            continue
        out[path[len(prefix):]] = content
    return out
