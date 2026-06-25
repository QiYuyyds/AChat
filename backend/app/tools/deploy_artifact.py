"""deploy_artifact tool — local static deployment for a web_app artifact.

Port of src/server/tools/deploy-artifact.ts. Returns a stable previewPath plus
downloadable packages. ``deploy_artifact_for_conversation`` and
``maybe_publish_externally`` are reused by deploy_workspace and the deploy
slash-command wiring.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Artifact
from app.schemas.messages import DeployStatusRecord
from app.services.deployment_service import (
    create_local_static_deployment,
    publish_deployment_to_static_directory,
)
from app.services.settings_service import get_app_settings
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.utils.artifact_preview import artifact_preview_path
from app.utils.clock import now_ms
from app.utils.ids import new_deployment_id

_EXTERNAL_SUMMARY_INSTRUCTION = (
    "User-facing summaries may quote the returned previewPath/publicUrl exactly. Do "
    "not invent or rewrite hostnames. If localPreviewPath is present, mention it only "
    "as a local fallback inside AgentHub."
)


class _Args(BaseModel):
    artifact_id: str = Field(alias="artifactId", min_length=1)

    model_config = {"populate_by_name": True}


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["artifactId"],
    "properties": {
        "artifactId": {
            "type": "string",
            "description": "Id of the web_app artifact to deploy, format art_xxx",
        },
    },
}


def _failed_deployment(
    artifact_id: str, title: str, error: str, version: int = 0
) -> DeployStatusRecord:
    return DeployStatusRecord(
        id=new_deployment_id(),
        artifactId=artifact_id,
        title=title,
        version=version,
        previewPath=artifact_preview_path(artifact_id),
        status="failed",
        sourceType="artifact",
        error=error,
        createdAt=now_ms(),
    )


async def deploy_artifact_for_conversation(
    conversation_id: str, artifact_id: str
) -> DeployStatusRecord:
    async with get_db() as db:
        result = await db.execute(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.conversation_id == conversation_id,
            )
        )
        artifact = result.scalar_one_or_none()
    if artifact is None:
        return _failed_deployment(artifact_id, "Unknown artifact", "Artifact not found")

    content = artifact.content_dict
    if content.get("type") != "web_app":
        return _failed_deployment(
            artifact.id,
            artifact.title,
            f'Artifact type "{content.get("type")}" cannot be deployed as a web app',
            artifact.version,
        )

    try:
        local = create_local_static_deployment(
            id=new_deployment_id(),
            artifact_id=artifact.id,
            title=artifact.title,
            version=artifact.version,
            content=content,
        )
    except (ValueError, OSError) as e:
        return _failed_deployment(artifact.id, artifact.title, str(e), artifact.version)
    return await maybe_publish_externally(local)


async def maybe_publish_externally(local: DeployStatusRecord) -> DeployStatusRecord:
    settings = await get_app_settings()
    if not settings.deployment_publish_enabled:
        return local

    if not settings.deployment_publish_dir or not settings.deployment_public_base_url:
        return local.model_copy(
            update={
                "status": "failed",
                "deployment_type": "external_static",
                "local_preview_path": local.preview_path,
                "error": (
                    "External static publishing is enabled, but deployment publish "
                    "directory or public base URL is not configured"
                ),
            }
        )

    try:
        published = publish_deployment_to_static_directory(
            local.id,
            publish_dir=settings.deployment_publish_dir,
            public_base_url=settings.deployment_public_base_url,
        )
    except (ValueError, OSError) as e:
        return local.model_copy(
            update={
                "status": "failed",
                "deployment_type": "external_static",
                "local_preview_path": local.preview_path,
                "error": f"External static publish failed: {e}",
            }
        )

    return local.model_copy(
        update={
            "preview_path": published.public_url,
            "deployment_path": published.public_url,
            "deployment_type": "external_static",
            "local_preview_path": local.preview_path,
            "public_url": published.public_url,
            "publish_path": published.publish_path,
            "publish_target_type": published.publish_target_type,
            "summary_instruction": _EXTERNAL_SUMMARY_INSTRUCTION,
        }
    )


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")
    record = await deploy_artifact_for_conversation(ctx.conversation_id, parsed.artifact_id)
    return ok(record.model_dump(by_alias=True, exclude_none=True))


deploy_artifact_tool = ToolDef(
    name="deploy_artifact",
    description=(
        "Create a local static deployment for a web_app artifact and return its "
        "stable previewPath plus downloadable packages. The previewPath is a relative "
        "path for the current AgentHub instance; do not invent or print a public "
        "hostname. In user-facing summaries, tell the user to use the deployment card "
        "buttons or quote previewPath exactly."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
