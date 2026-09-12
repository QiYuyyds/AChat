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
