"""Memory workspace directory management.

Creates and validates the three-level lifecycle directory structure:
  session/  → daily/  → digest/{procedure,wiki}/
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)


class MemoryWorkspace:
    """Manages the memory workspace directory tree."""

    def __init__(self, settings: Settings):
        self.root: Path = settings.memory_workspace_path
        self.session_dir: Path = self.root / "session"
        self.daily_dir: Path = self.root / "daily"
        self.digest_dir: Path = self.root / "digest"
        self.metadata_dir: Path = self.root / "metadata"
        self._initialized = False

    def initialize(self) -> None:
        """Create the directory tree if it doesn't exist."""
        for d in [self.root, self.session_dir, self.daily_dir, self.digest_dir, self.metadata_dir]:
            d.mkdir(parents=True, exist_ok=True)
        for bucket in ["procedure", "wiki"]:
            (self.digest_dir / bucket).mkdir(parents=True, exist_ok=True)
            if bucket == "procedure":
                (self.digest_dir / bucket / "shared").mkdir(parents=True, exist_ok=True)
                (self.digest_dir / bucket / "agents").mkdir(parents=True, exist_ok=True)
        self._initialized = True
        logger.info("MemoryWorkspace initialized at %s", self.root)

    # ─── Session files ───────────────────────────────────────────────────

    def session_path(self, conversation_id: str) -> Path:
        return self.session_dir / f"{conversation_id}.jsonl"

    # ─── Daily files ─────────────────────────────────────────────────────

    def daily_dir_for_date(self, day: str | None = None) -> Path:
        """Get the daily directory for a date (YYYY-MM-DD). Defaults to today."""
        d = day or date.today().isoformat()
        p = self.daily_dir / d
        p.mkdir(parents=True, exist_ok=True)
        return p

    def daily_file_path(self, name: str, day: str | None = None) -> Path:
        """Get the path for a daily card file."""
        d = day or date.today().isoformat()
        return self.daily_dir / d / f"{name}.md"

    def interests_path(self, day: str | None = None) -> Path:
        """Get the path for daily interests.yaml."""
        d = day or date.today().isoformat()
        return self.daily_dir / d / "interests.yaml"

    # ─── Digest files ────────────────────────────────────────────────────

    def digest_path(self, bucket: str, name: str, agent_id: str | None = None) -> Path:
        """Get the digest file path for a given bucket and name.

        For procedure bucket with agent_id → agents/<agent_id>/<name>.md
        For procedure bucket without agent_id → shared/<name>.md
        For wiki bucket → <name>.md (always global)
        """
        safe_name = name.replace("/", "-").replace("\\", "-")
        if bucket == "procedure":
            if agent_id:
                d = self.digest_dir / "procedure" / "agents" / agent_id
                d.mkdir(parents=True, exist_ok=True)
            else:
                d = self.digest_dir / "procedure" / "shared"
                d.mkdir(parents=True, exist_ok=True)
        else:
            d = self.digest_dir / "wiki"
            d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe_name}.md"

    def list_digest_files(self, bucket: str | None = None, agent_id: str | None = None) -> list[Path]:
        """List all digest Markdown files, optionally filtered by bucket/agent."""
        results: list[Path] = []
        if bucket and bucket != "procedure":
            d = self.digest_dir / bucket
            if d.exists():
                results.extend(sorted(d.glob("*.md")))
        elif bucket == "procedure":
            if agent_id:
                d = self.digest_dir / "procedure" / "agents" / agent_id
                if d.exists():
                    results.extend(sorted(d.glob("*.md")))
            shared = self.digest_dir / "procedure" / "shared"
            if shared.exists():
                results.extend(sorted(shared.glob("*.md")))
            if not agent_id:
                agents_dir = self.digest_dir / "procedure" / "agents"
                if agents_dir.exists():
                    results.extend(sorted(agents_dir.rglob("*.md")))
        else:
            for sub in self.digest_dir.rglob("*.md"):
                results.append(sub)
            results.sort()
        return results

    def list_daily_files(self, day: str | None = None) -> list[Path]:
        """List all daily card Markdown files for a given date (or all dates)."""
        if day:
            d = self.daily_dir / day
            if d.exists():
                return sorted(d.glob("*.md"))
            return []
        results = sorted(self.daily_dir.rglob("*.md"))
        return results

    def count_unprocessed_daily_cards(self) -> int:
        """Count daily cards that don't have corresponding digest entries.

        A simple heuristic: count .md files under daily/ directories that
        are not named 'interests.yaml'.
        """
        count = 0
        if self.daily_dir.exists():
            for f in self.daily_dir.rglob("*.md"):
                count += 1
        return count
