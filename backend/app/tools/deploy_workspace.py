"""deploy_workspace tool — deployment card from a static workspace directory.

Port of src/server/tools/deploy-workspace.ts. Copies existing static files from
a build output dir (dist/build/out/...); it does not run build commands.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Workspace
from app.schemas.messages import DeployStatusRecord
from app.services.deployment_service import create_workspace_static_deployment
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.deploy_artifact import maybe_publish_externally
from app.utils.clock import now_ms
from app.utils.ids import new_deployment_id
from app.utils.workspace_utils import assert_path_within_workspace, get_effective_cwd


class _Args(BaseModel):
    path: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    entry: str | None = Field(default=None, min_length=1)


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["path"],
    "properties": {
        "path": {
            "type": "string",
            "description": (
                'Static output directory inside the workspace, for example "dist", '
                '"build", "out", "client/dist", or "apps/web/dist".'
            ),
        },
        "title": {
            "type": "string",
            "description": (
                "Optional human-readable deployment title. Defaults to the directory "
                "name."
            ),
        },
        "entry": {
            "type": "string",
            "description": "Optional HTML entry file relative to path. Defaults to index.html.",
        },
    },
}


def _failed_workspace_deployment(
    workspace_path: str, error: str, title: str | None = None
) -> DeployStatusRecord:
    return DeployStatusRecord(
        id=new_deployment_id(),
        artifactId=f"workspace:{workspace_path}",
        title=title or f"Workspace {workspace_path}",
        version=0,
        previewPath="",
        status="failed",
        sourceType="workspace",
        workspacePath=workspace_path,
        error=error,
        createdAt=now_ms(),
    )


async def deploy_workspace_for_conversation(
    conversation_id: str, args: dict[str, Any]
) -> DeployStatusRecord:
    path = args["path"]
    title_arg = args.get("title")
    entry = args.get("entry")

    async with get_db() as db:
        result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == conversation_id)
        )
        workspace = result.scalar_one_or_none()
    if workspace is None:
        return _failed_workspace_deployment(path, "Workspace not found")

    try:
        source_dir = assert_path_within_workspace(workspace, path)
    except ValueError as e:
        return _failed_workspace_deployment(path, str(e) or "Deployment path is outside workspace")

    cwd = get_effective_cwd(workspace)
    workspace_path = os.path.relpath(source_dir, cwd).replace(os.sep, "/")
    if workspace_path in ("", "."):
        workspace_path = "."
    title = (title_arg.strip() if title_arg and title_arg.strip() else None) or f"Workspace {workspace_path}"

    try:
        local = create_workspace_static_deployment(
            id=new_deployment_id(),
            title=title,
            source_dir=source_dir,
            workspace_path=workspace_path,
            entry=entry,
        )
    except (ValueError, OSError) as e:
        return _failed_workspace_deployment(workspace_path, str(e), title)
    return await maybe_publish_externally(local)


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")
    record = await deploy_workspace_for_conversation(
        ctx.conversation_id,
        {"path": parsed.path, "title": parsed.title, "entry": parsed.entry},
    )
    return ok(record.model_dump(by_alias=True, exclude_none=True))


deploy_workspace_tool = ToolDef(
    name="deploy_workspace",
    description=(
        "Create a deployment card from a static directory inside the current "
        "workspace, such as dist, build, out, or client/dist. Use this after building "
        "a local project. It copies existing static files only; it does not run "
        "npm/pnpm/build commands. The directory must contain index.html unless entry "
        "is provided."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
