"""auto_dream — refine daily cards into digest memory.

Three-step pipeline:
1. extract — scan daily files, LLM identifies reusable abstractions
2. integrate — for each unit, search existing digest for dedup, then CREATE/CORROBORATE/REFINE/CORRECT
3. topics — select top-N interest topics, write interests.yaml
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import yaml

from app.memory.file_store.frontmatter import MemoryFrontmatter
from app.memory.file_store.markdown_io import read_markdown, write_markdown
from app.memory.file_store.wikilinks import add_wikilink, extract_wikilinks
from app.memory.file_store.workspace import MemoryWorkspace
from app.memory.search.hybrid_search import HybridSearch

logger = logging.getLogger(__name__)

_EXTRACT_SYSTEM_PROMPT = """\
You are a Memory Refinement Engine. Given daily memory cards, identify reusable
abstractions that belong in long-term memory.

## Bucket Classification
- **procedure**: How-to experience, task patterns, deployment steps, debugging strategies
- **wiki**: Knowledge nodes, concepts, technical facts, architecture decisions

## Output Format
Return JSON with a "units" array. Each unit has:
- "name": concise title (will be the filename stem)
- "bucket": "procedure" or "wiki"
- "summary": 1-2 sentence summary of the reusable knowledge
- "content": detailed Markdown body with optional wikilinks [[target]]
- "tags": 3-5 retrieval keywords
- "importance": 0.0-1.0
- "topics": optional list of interest topic titles

{"units": [
  {"name": "...", "bucket": "procedure", "summary": "...", "content": "...", "tags": ["..."], "importance": 0.6}
]}

If nothing worth refining, return: {"units": []}

Match the language of the input cards.
"""

_INTEGRATE_SYSTEM_PROMPT = """\
You are a Memory Integration Engine. You are given a new memory unit and
existing memory files that may be related. Decide what action to take:

- **CREATE**: No existing memory matches — create a new file
- **CORROBORATE**: Existing memory is confirmed — add evidence/source, update importance
- **REFINE**: Existing memory can be improved — merge new details, update content
- **CORRECT**: Existing memory is wrong — replace with corrected version

## Output Format
Return JSON with:
{"action": "CREATE|CORROBORATE|REFINE|CORRECT", "content": "updated markdown body", "reason": "..."}

If action is CREATE, content is the new memory body.
If action is CORROBORATE, content is the existing body with minor additions.
If action is REFINE, content is the merged body.
If action is CORRECT, content is the corrected body.

Match the language of the input.
"""


def _strip_code_fence(raw: str) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"^```json", "", raw)
    raw = re.sub(r"^```", "", raw)
    raw = re.sub(r"```$", "", raw)
    return raw.strip()


class AutoDream:
    """Three-step memory refinement pipeline: extract → integrate → topics."""

    def __init__(
        self,
        workspace: MemoryWorkspace,
        search: HybridSearch,
    ):
        self.workspace = workspace
        self.search = search
        self._generate_fn: Callable | None = None

    def set_generate_fn(self, fn: Callable) -> None:
        self._generate_fn = fn

    async def run(self, max_units: int = 5) -> dict:
        """Run the full auto_dream pipeline.

        Returns a summary dict with counts: {extracted, created, updated, topics}.
        """
        if not self._generate_fn:
            logger.warning("auto_dream: skipped — no generate_fn injected")
            return {"extracted": 0, "created": 0, "updated": 0, "topics": 0}

        # Step 1: Extract
        units = await self._dream_extract(max_units)
        logger.info("auto_dream: extracted %d units", len(units))
        if not units:
            return {"extracted": 0, "created": 0, "updated": 0, "topics": 0}

        # Step 2: Integrate
        created = 0
        updated = 0
        for unit in units:
            action = await self._dream_integrate(unit)
            if action == "created":
                created += 1
            elif action in ("updated", "refined", "corrected"):
                updated += 1

        # Step 3: Topics
        topics_count = await self._dream_topics(units)

        return {
            "extracted": len(units),
            "created": created,
            "updated": updated,
            "topics": topics_count,
        }

    async def _dream_extract(self, max_units: int) -> list[dict]:
        """Scan recent daily files and extract reusable abstractions via LLM."""
        # Gather recent daily files (last 7 days)
        daily_contents: list[str] = []
        today = date.today()
        for days_ago in range(7):
            d = (today - timedelta(days=days_ago)).isoformat()
            day_dir = self.workspace.daily_dir / d
            if day_dir.exists():
                for f in sorted(day_dir.glob("*.md")):
                    mem = read_markdown(f)
                    if mem:
                        daily_contents.append(f"### {mem.frontmatter.name}\n\n{mem.body}")

        if not daily_contents:
            return []

        prompt = "## Daily Memory Cards\n\n" + "\n\n---\n\n".join(daily_contents)

        try:
            raw = await asyncio.to_thread(self._generate_fn, _EXTRACT_SYSTEM_PROMPT, prompt)
        except Exception as e:
            logger.warning("auto_dream extract LLM call failed: %s", e)
            return []

        raw = _strip_code_fence(raw)
        logger.debug("auto_dream extract raw output: %s", raw[:500])
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("auto_dream extract: LLM output not valid JSON: %s — output was: %s", e, raw[:300])
            return []

        if not isinstance(parsed, dict):
            return []

        units = parsed.get("units", [])
        if not isinstance(units, list):
            return []

        return units[:max_units]

    async def _dream_integrate(self, unit: dict) -> str:
        """Integrate a single extracted unit: search existing, then CREATE/REFINE/etc.

        Returns the action taken: 'created', 'updated', 'refined', 'corrected', or 'skipped'.
        """
        name = str(unit.get("name", "")).strip()
        if not name:
            return "skipped"

        bucket = str(unit.get("bucket", "wiki")).strip()
        if bucket not in ("procedure", "wiki"):
            bucket = "wiki"

        summary = str(unit.get("summary", "")).strip()
        content = str(unit.get("content", "")).strip()
        tags = [str(t) for t in (unit.get("tags") or []) if t]
        importance = float(unit.get("importance", 0.5) or 0.5)
        today = date.today().isoformat()

        # Search for existing digest entries
        existing = await self.search.search(query=name, top_k=3)

        if not existing:
            # CREATE: no match found
            fm = MemoryFrontmatter(
                name=name,
                description=summary,
                tags=tags[:10],
                importance=importance,
                bucket=bucket,
                created_at=today,
                updated_at=today,
                source="auto_dream",
            )
            filepath = self.workspace.digest_path(bucket, name, agent_id=unit.get("agent_id"))
            write_markdown(filepath, fm, content)
            logger.info("auto_dream CREATE: %s → %s", name, filepath)
            return "created"

        # Use LLM to decide action (CREATE/CORROBORATE/REFINE/CORRECT)
        existing_context = "\n---\n".join(
            f"### {r.name}\nPath: {r.path}\nContent: {r.content[:500]}"
            for r in existing
        )

        prompt = (
            f"## New Memory Unit\n"
            f"Name: {name}\n"
            f"Bucket: {bucket}\n"
            f"Summary: {summary}\n"
            f"Content: {content}\n\n"
            f"## Existing Memory Files\n"
            f"{existing_context}\n\n"
            f"Decide the action and provide the final content."
        )

        try:
            raw = await asyncio.to_thread(self._generate_fn, _INTEGRATE_SYSTEM_PROMPT, prompt)
        except Exception as e:
            logger.warning("auto_dream integrate LLM call failed: %s", e)
            return "skipped"

        raw = _strip_code_fence(raw)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("auto_dream integrate: LLM output not valid JSON: %s", raw[:200])
            return "skipped"

        if not isinstance(parsed, dict):
            return "skipped"

        action = str(parsed.get("action", "CREATE")).strip().upper()
        final_content = str(parsed.get("content", content)).strip() or content

        if action == "CREATE":
            fm = MemoryFrontmatter(
                name=name,
                description=summary,
                tags=tags[:10],
                importance=importance,
                bucket=bucket,
                created_at=today,
                updated_at=today,
                source="auto_dream",
            )
            filepath = self.workspace.digest_path(bucket, name, agent_id=unit.get("agent_id"))
            write_markdown(filepath, fm, final_content)
            return "created"

        # CORROBORATE / REFINE / CORRECT → update the top match
        top_match = existing[0]
        top_path = Path(top_match.path)
        mem = read_markdown(top_path)
        if mem is None:
            return "skipped"

        # Merge: update content, bump importance, add wikilink to new source
        updated_content = final_content
        if action in ("CORROBORATE", "REFINE"):
            # Guard: limit Update sections to prevent infinite duplication (max 3)
            update_count = mem.body.count("## Update (")
            if update_count >= 3:
                logger.info(
                    "auto_dream %s: skipping append — %s already has %d updates (max 3)",
                    action, name, update_count,
                )
                updated_content = mem.body  # still bump metadata, don't append
            elif final_content not in mem.body:
                updated_content = mem.body.rstrip() + f"\n\n---\n## Update ({today})\n\n{final_content}"
            else:
                logger.info("auto_dream %s: content duplicate in %s, skipping append", action, name)
                updated_content = mem.body

        # Add wikilink from existing to new name
        if name != mem.frontmatter.name:
            updated_content = add_wikilink(updated_content, name)

        merged_tags = list(dict.fromkeys(mem.frontmatter.tags + tags))[:10]
        mem.frontmatter.importance = max(mem.frontmatter.importance, importance)
        mem.frontmatter.tags = merged_tags
        mem.frontmatter.updated_at = today

        write_markdown(top_path, mem.frontmatter, updated_content)
        logger.info("auto_dream %s: %s → %s", action, name, top_path)
        return action.lower()

    async def _dream_topics(self, units: list[dict]) -> int:
        """Select top-N interest topics and write interests.yaml."""
        topics: list[dict] = []
        for unit in units:
            unit_topics = unit.get("topics") or []
            if isinstance(unit_topics, list) and unit_topics:
                for t in unit_topics:
                    topics.append({
                        "title": str(t),
                        "reason": unit.get("summary", ""),
                        "keywords": unit.get("tags", []),
                        "evidence": unit.get("name", ""),
                    })
            elif unit.get("name"):
                topics.append({
                    "title": unit.get("name", ""),
                    "reason": unit.get("summary", ""),
                    "keywords": unit.get("tags", []),
                    "evidence": unit.get("name", ""),
                })

        logger.info("auto_dream topics: collected %d raw topics from %d units", len(topics), len(units))
        if not topics:
            logger.warning("auto_dream topics: no topics collected — LLM may not have returned 'topics' field")
            return 0

        # Deduplicate against recent 7 days of interests
        today = date.today()
        recent_titles: set[str] = set()
        for days_ago in range(1, 8):
            d = (today - timedelta(days=days_ago)).isoformat()
            ipath = self.workspace.interests_path(d)
            if ipath.exists():
                try:
                    data = yaml.safe_load(ipath.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                t = str(item.get("title", "")).strip()
                                if t:
                                    recent_titles.add(t)
                except Exception:
                    pass

        fresh = [t for t in topics if t.get("title", "") not in recent_titles]
        top_topics = fresh[:3]  # default N=3
        logger.info("auto_dream topics: %d fresh after dedup (recent=%s)", len(top_topics), recent_titles or "none")

        if not top_topics:
            logger.info("auto_dream topics: all topics were duplicates of recent interests, skipping")
            return 0

        # Write today's interests.yaml
        ipath = self.workspace.interests_path()
        ipath.parent.mkdir(parents=True, exist_ok=True)
        with open(ipath, "w", encoding="utf-8") as f:
            yaml.dump(top_topics, f, allow_unicode=True, default_flow_style=False)
        logger.info("auto_dream: wrote %d topics to %s", len(top_topics), ipath)

        return len(top_topics)

    def should_trigger(self, threshold: int) -> bool:
        """Check if auto_dream should trigger based on daily card count."""
        return self.workspace.count_unprocessed_daily_cards() >= threshold
