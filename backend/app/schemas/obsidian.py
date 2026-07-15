"""Pydantic schemas for Obsidian sync API."""


from pydantic import BaseModel, Field


class SyncRequest(BaseModel):
    """Request to trigger an Obsidian vault sync."""

    vault_path: str | None = Field(default=None, alias="vaultPath")

    model_config = {"populate_by_name": True}


class SyncError(BaseModel):
    """A single error from sync processing."""

    path: str
    error: str


class SyncResponse(BaseModel):
    """Response from POST /api/obsidian/sync."""

    scanned: int
    added: int
    updated: int
    deleted: int
    skipped: int
    errors: list[SyncError] = Field(default_factory=list)


class SyncStatus(BaseModel):
    """Response from GET /api/obsidian/status."""

    vault_path: str | None = Field(default=None, alias="vaultPath")
    vault_exists: bool = Field(default=False, alias="vaultExists")
    total_md_files: int = Field(default=0, alias="totalMdFiles")
    last_sync_at: float | None = Field(default=None, alias="lastSyncAt")
    last_sync_summary: SyncResponse | None = Field(default=None, alias="lastSyncSummary")

    model_config = {"populate_by_name": True}
