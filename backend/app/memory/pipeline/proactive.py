"""proactive — surface interest topics to agents.

Reads daily/<date>/interests.yaml and returns structured topics.
The host agent decides whether and how to mention them.
"""

from __future__ import annotations

import logging
from datetime import date

import yaml

from app.memory.file_store.workspace import MemoryWorkspace

logger = logging.getLogger(__name__)


class Proactive:
    """Read proactive topics from interests.yaml files."""

    def __init__(self, workspace: MemoryWorkspace):
        self.workspace = workspace

    def get_topics(self, day: str | None = None) -> list[dict]:
        """Read today's (or specified day's) interests.yaml.

        Returns a list of topic dicts with keys: title, reason, keywords, evidence.
        Returns empty list if no interests.yaml exists.
        """
        d = day or date.today().isoformat()
        ipath = self.workspace.interests_path(d)
        if not ipath.exists():
            return []

        try:
            data = yaml.safe_load(ipath.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("proactive: failed to read %s: %s", ipath, e)
            return []

        if not isinstance(data, list):
            return []

        topics: list[dict] = []
        for item in data:
            if isinstance(item, dict):
                topics.append({
                    "title": str(item.get("title", "")).strip(),
                    "reason": str(item.get("reason", "")).strip(),
                    "keywords": [str(k) for k in (item.get("keywords") or []) if k],
                    "evidence": str(item.get("evidence", "")).strip(),
                })
        return topics

    def format_for_prompt(self, topics: list[dict] | None = None) -> str:
        """Format topics as a system prompt section.

        If topics is None, reads today's topics.
        Returns empty string if no topics.
        """
        if topics is None:
            topics = self.get_topics()
        if not topics:
            return ""

        lines = ["【主动话题】"]
        for t in topics:
            title = t.get("title", "")
            reason = t.get("reason", "")
            if title:
                lines.append(f"- {title}: {reason}")

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)
