"""Frontmatter schema for memory Markdown files.

Each memory file has YAML frontmatter with:
  name, description, agent_id, tags, importance, bucket, created_at, updated_at, source
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class MemoryFrontmatter:
    """Validated frontmatter for a memory Markdown file."""

    name: str = ""
    description: str = ""
    agent_id: str | None = None
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    bucket: str = "wiki"  # procedure | wiki
    created_at: str = ""  # YYYY-MM-DD
    updated_at: str = ""  # YYYY-MM-DD
    source: str = ""  # relative path to source daily card

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "agent_id": self.agent_id,
            "tags": list(self.tags),
            "importance": self.importance,
            "bucket": self.bucket,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryFrontmatter:
        today = date.today().isoformat()
        return cls(
            name=str(data.get("name", "")).strip(),
            description=str(data.get("description", "")).strip(),
            agent_id=data.get("agent_id") or None,
            tags=[str(t) for t in (data.get("tags") or []) if t],
            importance=float(data.get("importance", 0.5) or 0.5),
            bucket=str(data.get("bucket", "wiki")).strip() or "wiki",
            created_at=str(data.get("created_at", today)).strip() or today,
            updated_at=str(data.get("updated_at", today)).strip() or today,
            source=str(data.get("source", "")).strip(),
        )

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []
        if not self.name:
            errors.append("name is required")
        if self.bucket not in ("procedure", "wiki"):
            errors.append(f"bucket must be 'procedure' or 'wiki', got '{self.bucket}'")
        if not (0.0 <= self.importance <= 1.0):
            errors.append(f"importance must be 0-1, got {self.importance}")
        return errors
