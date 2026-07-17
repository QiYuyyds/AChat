"""Validated atomic Workspace metadata for source intelligence."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROGRESS_TRANSPORT = "rest"

CodeIntelligenceStatus = Literal[
    "disabled",
    "preparing_runtime",
    "queued",
    "indexing",
    "ready",
    "syncing",
    "rebuilding",
    "cancelling",
    "failed",
    "interrupted",
]


class CodeIntelligenceCounts(BaseModel):
    files: int = Field(default=0, ge=0, le=1_000_000_000)
    symbols: int = Field(default=0, ge=0, le=1_000_000_000)
    relationships: int = Field(default=0, ge=0, le=1_000_000_000)


class CodeIntelligenceMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = False
    runtime_version: str | None = Field(default=None, alias="runtimeVersion", max_length=64)
    status: CodeIntelligenceStatus = "disabled"
    phase: str | None = Field(default=None, max_length=200)
    progress_percent: int | None = Field(
        default=None,
        alias="progressPercent",
        ge=0,
        le=99,
    )
    counts: CodeIntelligenceCounts = Field(default_factory=CodeIntelligenceCounts)
    created_at: int | None = Field(default=None, alias="createdAt", ge=0)
    updated_at: int | None = Field(default=None, alias="updatedAt", ge=0)
    started_at: int | None = Field(default=None, alias="startedAt", ge=0)
    completed_at: int | None = Field(default=None, alias="completedAt", ge=0)
    last_sync_at: int | None = Field(default=None, alias="lastSyncAt", ge=0)
    error: str | None = Field(default=None, max_length=2000)


class MetadataStore:
    def __init__(self, workspace_root: Path) -> None:
        self.path = Path(workspace_root) / ".agenthub" / "code-intelligence.json"

    def read(self) -> CodeIntelligenceMetadata:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return CodeIntelligenceMetadata()
        return CodeIntelligenceMetadata.model_validate(raw)

    def write(self, metadata: CodeIntelligenceMetadata) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp-{uuid.uuid4().hex}")
        payload = json.dumps(
            metadata.model_dump(by_alias=True),
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)
