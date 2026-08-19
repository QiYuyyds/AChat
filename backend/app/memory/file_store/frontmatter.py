"""Frontmatter schema for memory Markdown files.

Each memory file has YAML frontmatter with:
  name, description, agent_id, tags, importance, bucket, stable_key,
  created_at, updated_at, source
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.memory.buckets import DIGEST_BUCKETS, make_stable_key, normalize_bucket


@dataclass
class MemoryFrontmatter:
    """Validated frontmatter for a memory Markdown file."""

    name: str = ""
    description: str = ""
    agent_id: str | None = None
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    bucket: str = "wiki"  # procedure | personal | wiki
    status: str = "active"  # active | archived
    created_at: str = ""  # YYYY-MM-DD
    updated_at: str = ""  # YYYY-MM-DD
    source: str = ""  # relative path to source daily card
    stable_key: str = ""  # merge key across title variants

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "agent_id": self.agent_id,
            "tags": list(self.tags),
            "importance": self.importance,
            "bucket": self.bucket,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
            "stable_key": self.stable_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryFrontmatter:
        today = date.today().isoformat()
        name = str(data.get("name", "")).strip()
        bucket = normalize_bucket(str(data.get("bucket", "wiki")))
        stable_key = str(data.get("stable_key", "")).strip()
        if not stable_key and name:
            stable_key = make_stable_key(bucket, name)
        return cls(
            name=name,
            description=str(data.get("description", "")).strip(),
            agent_id=data.get("agent_id") or None,
            tags=[str(t) for t in (data.get("tags") or []) if t],
            importance=float(data.get("importance", 0.5) or 0.5),
            bucket=bucket,
            status=str(data.get("status", "active")).strip() or "active",
            created_at=str(data.get("created_at", today)).strip() or today,
            updated_at=str(data.get("updated_at", today)).strip() or today,
            source=str(data.get("source", "")).strip(),
            stable_key=stable_key,
        )

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []
        if not self.name:
            errors.append("name is required")
        if self.bucket not in DIGEST_BUCKETS:
            errors.append(
                f"bucket must be one of {DIGEST_BUCKETS}, got '{self.bucket}'"
            )
        if self.status not in ("active", "archived"):
            errors.append(f"status must be 'active' or 'archived', got '{self.status}'")
        if not (0.0 <= self.importance <= 1.0):
            errors.append(f"importance must be 0-1, got {self.importance}")
        return errors
