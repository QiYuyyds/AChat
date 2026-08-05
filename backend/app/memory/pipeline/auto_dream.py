"""auto_dream — refine daily cards into digest memory.

Three-step pipeline:
1. extract — scan CHANGED daily files (via file catalog), LLM identifies reusable
   abstractions with cross-file merge guidance and structured topic candidates
2. integrate — for each unit, node_search existing digest → LLM with per-bucket
   prompt (procedure=runbook / wiki=encyclopedia) → program writes with
   provenance wikilinks and related-node weaving
3. topics — LLM selects top-N interest topics (with fallback to rule-based)

Enhancements over original:
  - dream_extract: file catalog change detection (only process changed files)
  - dream_extract: cross-file merge guidance + structured topic candidates
  - dream_integrate: per-bucket prompts (procedure / wiki)
  - dream_integrate: provenance wikilinks (derived_from:: [[daily/<path>]])
  - dream_integrate: related-node wikilink weaving
  - dream_integrate: node_search for digest recall
  - dream_integrate: skip archived nodes
  - dream_topics: LLM selection with Topic Quality guidance + fallback
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

from app.memory.file_store.file_catalog import FileCatalog
from app.memory.file_store.frontmatter import MemoryFrontmatter
from app.memory.file_store.markdown_io import read_markdown, write_markdown
from app.memory.file_store.wikilinks import add_wikilink
from app.memory.file_store.workspace import MemoryWorkspace
from app.memory.search.hybrid_search import HybridSearch
from app.memory.search.node_search import NodeSearch

logger = logging.getLogger(__name__)

_EXTRACT_SYSTEM_PROMPT = """\
You are a Memory Refinement Engine. Given daily memory cards, identify reusable
abstractions that belong in long-term memory.

## Quality Gate
- "Prefer fewer, richer units over exhaustive file summaries" — 反摘要指导
- "This extraction step is the gate for not worth memorizing" — 质量闸口
- "Do not emit passing mentions, known-concept recaps, one-off timestamps, attendance facts, or facts with no reusable value" — 噪声过滤
- "Merge evidence from multiple files when it teaches the same abstraction" — 跨文件合并指导

## Bucket Classification
- **procedure**: How-to experience, task patterns, deployment steps, debugging strategies
- **personal**: user/team/project-specific identity, preferences, conventions, constraints, avoidances
- **wiki**: Knowledge nodes, concepts, technical facts, architecture decisions

## Cross-File Merging
When multiple daily files teach the same abstraction or concept, merge their
evidence into a single unit. The unit's content should synthesize insights
from all contributing files.

## Output Format
Return JSON with a "units" array and a "topic_candidates" array:

{
  "units": [
    {
      "name": "concise title",
      "bucket": "procedure" or "personal" or "wiki",
      "summary": "1-2 sentence summary",
      "content": "detailed Markdown body",
      "tags": ["tag1", "tag2"],
      "importance": 0.6,
      "source_paths": ["daily/2026-08-04/session1.md"]
    }
  ],
  "topic_candidates": [
    {
      "title": "interest topic title",
      "reason": "why this is interesting",
      "evidence": "which unit supports this",
      "keywords": ["keyword1"],
      "paths": ["daily/2026-08-04/session1.md"]
    }
  ]
}

If nothing worth refining, return: {"units": [], "topic_candidates": []}

Match the language of the input cards.

"""

_INTEGRATE_SYSTEM_PROMPT_PROCEDURE = """\
You are a Memory Integration Engine for PROCEDURE-type memories (how-to, runbooks).

## Workflow
1. Recall: Review the new memory unit and any existing digest nodes found by search
2. Classify: Decide what action to take
3. Act: Generate the final content
4. Weave: Add wikilinks to related nodes

## Actions
- **CREATE**: No existing memory matches — output the COMPLETE body for a new file
- **CORROBORATE**: Existing memory is confirmed — output only the NEW evidence/source to append
- **REFINE**: Existing memory can be improved — output only the NEW details to append
- **CORRECT**: Existing memory is wrong — output the COMPLETE corrected replacement body

## Body Shape: Runbook
For CREATE and CORRECT, write the complete body as a structured runbook:
```
## Trigger
<when to use this procedure>

## Steps
1. <step>
2. <step>

## Pre-conditions
<what must be true before starting>

## Failure Modes
<what can go wrong and how to recover>
```
For CORROBORATE and REFINE, output only the new content to append (e.g. a new
failure mode, a new step, or corroborating evidence). Do NOT repeat existing content.

## Wikilink Rules
- Add `derived_from:: [[daily/<date>/<session>.md]]` pointing to source daily card(s)
- Add `relates_to:: [[digest/<bucket>/<name>.md]]` for related digest nodes
- Preserve existing `derived_from::` wikilinks (additive only)
- UPDATE must be additive: never remove existing wikilinks or derived_from entries
- Default to weaving more, not less

## Output Format
Return JSON:
{
  "action": "CREATE|CORROBORATE|REFINE|CORRECT",
  "content": "for CREATE/CORRECT: complete body; for CORROBORATE/REFINE: only new content to append",
  "reason": "why this action",
  "wikilinks": ["derived_from:: [[daily/...]]", "relates_to:: [[digest/...]]"]
}

Match the language of the input.

"""

_INTEGRATE_SYSTEM_PROMPT_WIKI = """\
You are a Memory Integration Engine for WIKI-type memories (concepts, knowledge nodes).

## Workflow
1. Recall: Review the new memory unit and any existing digest nodes found by search
2. Classify: Decide what action to take
3. Act: Generate the final content
4. Weave: Add wikilinks to related nodes

## Actions
- **CREATE**: No existing memory matches — output the COMPLETE body for a new file
- **CORROBORATE**: Existing memory is confirmed — output only the NEW evidence/source to append
- **REFINE**: Existing memory can be improved — output only the NEW details to append
- **CORRECT**: Existing memory is wrong — output the COMPLETE corrected replacement body

## Body Shape: Encyclopedia
For CREATE and CORRECT, write the complete body as an encyclopedia entry:
```
<First line: one-sentence definition>

## Properties
- <property>: <description>

## Context
<additional context, history, or usage notes>
```
For CORROBORATE and REFINE, output only the new content to append (e.g. a new
property, extra context, or corroborating evidence). Do NOT repeat existing content.

## Wikilink Rules
- Add `derived_from:: [[daily/<date>/<session>.md]]` pointing to source daily card(s)
- Add `relates_to:: [[digest/<bucket>/<name>.md]]` for related digest nodes
- Preserve existing `derived_from::` wikilinks (additive only)
- UPDATE must be additive: never remove existing wikilinks or derived_from entries
- Default to weaving more, not less

## Output Format
Return JSON:
{
  "action": "CREATE|CORROBORATE|REFINE|CORRECT",
  "content": "for CREATE/CORRECT: complete body; for CORROBORATE/REFINE: only new content to append",
  "reason": "why this action",
  "wikilinks": ["derived_from:: [[daily/...]]", "relates_to:: [[digest/...]]"]
}

Match the language of the input.

"""

_INTEGRATE_SYSTEM_PROMPT_PERSONAL = """\
You are a Memory Integration Engine for PERSONAL-type memories (user/team/project-specific identity, preferences, conventions, constraints, avoidances).

## Workflow
1. Recall: Review the new memory unit and any existing digest nodes found by search
2. Classify: Decide what action to take
3. Act: Generate the final content
4. Weave: Add wikilinks to related nodes

## Actions
- **CREATE**: No existing memory matches — output the COMPLETE body for a new file
- **CORROBORATE**: Existing memory is confirmed — output only the NEW evidence/source to append
- **REFINE**: Existing memory can be improved — output only the NEW details to append
- **CORRECT**: Existing memory is wrong — output the COMPLETE corrected replacement body

## Body Shape: Rule of Engagement
For CREATE and CORRECT, write the complete body as a rule of engagement:
```
## Rule / fact
<one sentence stating the preference, convention, identity fact, constraint, or avoid-rule>

## Why
<reason or context for this rule>

## How to apply
<applicable contexts, tasks, boundaries, or exceptions>
```
For CORROBORATE and REFINE, output only the new content to append (e.g. additional context, refined application, or supporting evidence). Do NOT repeat existing content.

## Wikilink Rules
- Add `derived_from:: [[daily/<date>/<session>.md]]` pointing to source daily card(s)
- Add `relates_to:: [[digest/<bucket>/<name>.md]]` for related digest nodes
- Preserve existing `derived_from::` wikilinks (additive only)
- UPDATE must be additive: never remove existing wikilinks or derived_from entries
- Default to weaving more, not less

## Output Format
Return JSON:
{
  "action": "CREATE|CORROBORATE|REFINE|CORRECT",
  "content": "for CREATE/CORRECT: complete body; for CORROBORATE/REFINE: only new content to append",
  "reason": "why this action",
  "wikilinks": ["derived_from:: [[daily/...]]", "relates_to:: [[digest/...]]"]
}

Match the language of the input.

"""

_TOPICS_SYSTEM_PROMPT = """\
You are a Memory Topics Curator. Given candidate topics from today's memory
extraction and existing recent topics, select the most valuable ones.

## Topic Quality Criteria
- Specific and actionable (not vague labels)
- Recurring across multiple sessions
- Teaches a reusable lesson or skill
- Has enough evidence to be useful

## Selection Rules
- Prefer specific topics over generic ones
- Deduplicate against recent 7-day topics (similar meaning = duplicate)
- Keep same-day existing topics (don't overwrite)
- Select up to N topics (default 3)

## Output Format
Return JSON:
{
  "topics": [
    {
      "title": "topic title",
      "reason": "why selected",
      "keywords": ["keyword1"],
      "evidence": "supporting evidence"
    }
  ]
}

If no valuable topics, return: {"topics": []}

Match the language of the input.
"""


def _strip_code_fence(raw: str) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"^```json", "", raw)
    raw = re.sub(r"^```", "", raw)
    raw = re.sub(r"```$", "", raw)
    return raw.strip()


def _dedupe_by_path(hits_1: list, hits_2: list) -> list:
    """Merge two search result lists and deduplicate by path, keeping higher score."""
    path_to_best = {}
    
    # Process both lists
    for hit in hits_1 + hits_2:
        path = getattr(hit, 'path', '')
        score = getattr(hit, 'score', 0.0)
        
        if not path:
            continue
            
        if path not in path_to_best or score > getattr(path_to_best[path], 'score', 0.0):
            path_to_best[path] = hit
    
    # Return top-5 by score
    deduped = sorted(path_to_best.values(), 
                    key=lambda h: getattr(h, 'score', 0.0), 
                    reverse=True)[:5]
    return deduped


def _normalize_topic_title(title: str) -> str:
    """Normalize a topic title for deduplication.
    
    Lowercase, remove punctuation, compress whitespace.
    """
    title = title.lower().strip()
    title = re.sub(r"[^\w\s]", "", title)  # Remove punctuation
    title = re.sub(r"\s+", " ", title)      # Compress whitespace
    return title


class AutoDream:
    """Three-step memory refinement pipeline: extract → integrate → topics."""

    def __init__(
        self,
        workspace: MemoryWorkspace,
        search: HybridSearch,
        node_search: NodeSearch | None = None,
        file_catalog: FileCatalog | None = None,
    ):
        self.workspace = workspace
        self.search = search
        self.node_search = node_search
        self.file_catalog = file_catalog
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
        units, topic_candidates = await self._dream_extract(max_units)
        logger.info("auto_dream: extracted %d units, %d topic candidates", len(units), len(topic_candidates))
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
        topics_count = await self._dream_topics(units, topic_candidates)

        return {
            "extracted": len(units),
            "created": created,
            "updated": updated,
            "topics": topics_count,
        }

    async def _dream_extract(self, max_units: int) -> tuple[list[dict], list[dict]]:
        """Scan changed daily files and extract reusable abstractions via LLM.

        Uses file catalog to only process changed files. Falls back to
        scanning recent 7 days if catalog is not available.
        Returns (units, topic_candidates).
        """
        daily_contents: list[str] = []
        changed_paths: list[str] = []

        # Get changed files from catalog
        if self.file_catalog:
            changed = self.file_catalog.get_changed(bucket="daily")
            for path_str, _bucket in changed:
                p = Path(path_str)
                if not p.exists():
                    self.file_catalog.remove(path_str)
                    continue
                # Skip .yaml files (input protection)
                if p.suffix.lower() == '.yaml':
                    continue
                mem = read_markdown(p)
                if mem:
                    daily_contents.append(f"### {mem.frontmatter.name}\n\n{mem.body}")
                    changed_paths.append(path_str)
            logger.info("auto_dream extract: %d changed files from catalog", len(changed_paths))

        # Fallback: scan recent 7 days if no catalog or no changes
        if not daily_contents:
            today = date.today()
            for days_ago in range(7):
                d = (today - timedelta(days=days_ago)).isoformat()
                day_dir = self.workspace.daily_dir / d
                if day_dir.exists():
                    for f in sorted(day_dir.glob("*.md")):
                        mem = read_markdown(f)
                        if mem:
                            daily_contents.append(f"### {mem.frontmatter.name}\n\n{mem.body}")
                            changed_paths.append(str(f))

        if not daily_contents:
            return [], []

        prompt = "## Daily Memory Cards\n\n" + "\n\n---\n\n".join(daily_contents)

        try:
            raw = await asyncio.to_thread(self._generate_fn, _EXTRACT_SYSTEM_PROMPT, prompt)
        except Exception as e:
            logger.warning("auto_dream extract LLM call failed: %s", e)
            return [], []

        raw = _strip_code_fence(raw)
        logger.debug("auto_dream extract raw output: %s", raw[:500])
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("auto_dream extract: LLM output not valid JSON: %s — output was: %s", e, raw[:300])
            return [], []

        if not isinstance(parsed, dict):
            return [], []

        units = parsed.get("units", [])
        if not isinstance(units, list):
            units = []

        topic_candidates = parsed.get("topic_candidates", [])
        if not isinstance(topic_candidates, list):
            topic_candidates = []

        # Validate source_paths and enrich units
        for unit in units:
            source_paths = unit.get("source_paths", [])
            # Filter source_paths to only include changed_paths
            valid_paths = []
            for path in source_paths:
                if path in changed_paths:
                    valid_paths.append(path)
            
            # If all paths were filtered out, fallback to changed_paths[:3]
            if not valid_paths and changed_paths:
                valid_paths = changed_paths[:3]
                logger.debug("auto_dream extract: source_paths filtered to empty, fallback to changed_paths[:3]")
            
            unit["source_paths"] = valid_paths

        return units[:max_units], topic_candidates

    async def _dream_integrate(self, unit: dict) -> str:
        """Integrate a single extracted unit: node_search → per-bucket LLM → write.

        Returns the action taken: 'created', 'updated', 'refined', 'corrected', or 'skipped'.
        """
        name = str(unit.get("name", "")).strip()
        if not name:
            return "skipped"

        bucket = str(unit.get("bucket", "wiki")).strip()
        if bucket not in ("procedure", "personal", "wiki"):
            bucket = "wiki"

        summary = str(unit.get("summary", "")).strip()
        content = str(unit.get("content", "")).strip()
        tags = [str(t) for t in (unit.get("tags") or []) if t]
        importance = float(unit.get("importance", 0.5) or 0.5)
        source_paths = unit.get("source_paths", [])
        today = date.today().isoformat()

        # Use node_search for digest recall (falls back to HybridSearch)
        existing_nodes: list = []
        if self.node_search:
            # Two-round search: name (exact) + summary (semantic expansion)
            hits_1 = self.node_search.search(query=name, bucket=bucket, limit=10)
            hits_2 = self.node_search.search(query=summary, bucket=bucket, limit=10)
            existing_nodes = _dedupe_by_path(hits_1, hits_2)
        else:
            # Fallback to HybridSearch (single query)
            existing = await self.search.search(query=name, top_k=3, bucket=bucket)
            existing_nodes = existing

        # Filter out archived nodes
        active_nodes = []
        for node in existing_nodes:
            # NodeSearchResult has frontmatter dict; SearchResult has frontmatter dict
            fm = node.frontmatter if hasattr(node, 'frontmatter') else {}
            if isinstance(fm, dict) and fm.get("status") == "archived":
                logger.info("auto_dream: skipping archived node %s", getattr(node, 'name', ''))
                continue
            active_nodes.append(node)

        # Select per-bucket prompt
        if bucket == "procedure":
            system_prompt = _INTEGRATE_SYSTEM_PROMPT_PROCEDURE
        elif bucket == "personal":
            system_prompt = _INTEGRATE_SYSTEM_PROMPT_PERSONAL
        else:
            system_prompt = _INTEGRATE_SYSTEM_PROMPT_WIKI

        # Build existing context for LLM
        existing_context = ""
        if active_nodes:
            context_parts = []
            for node in active_nodes:
                node_name = getattr(node, 'name', '')
                node_path = getattr(node, 'path', '')
                node_content = getattr(node, 'content', '')[:500]
                context_parts.append(f"### {node_name}\nPath: {node_path}\nContent: {node_content}")
            existing_context = "\n---\n".join(context_parts)

        # Build provenance wikilinks from source paths
        provenance_links = []
        for src in source_paths[:3]:
            provenance_links.append(f"derived_from:: [[{src}]]")

        prompt = (
            f"## New Memory Unit\n"
            f"Name: {name}\n"
            f"Bucket: {bucket}\n"
            f"Summary: {summary}\n"
            f"Content: {content}\n"
            f"Source paths: {', '.join(source_paths[:5])}\n"
            f"Suggested provenance: {chr(10).join(provenance_links)}\n\n"
        )
        if existing_context:
            prompt += f"## Existing Memory Files\n{existing_context}\n\n"
        prompt += "Decide the action and provide the final content with wikilinks."

        try:
            raw = await asyncio.to_thread(self._generate_fn, system_prompt, prompt)
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

        # Validate provenance: warn if no derived_from wikilink
        if "derived_from::" not in final_content:
            # Auto-append provenance links
            for link in provenance_links:
                if link not in final_content:
                    final_content = final_content.rstrip() + f"\n{link}"
            logger.debug("auto_dream: auto-appended provenance wikilinks to %s", name)

        if action == "CREATE" or not active_nodes:
            fm = MemoryFrontmatter(
                name=name,
                description=summary,
                tags=tags[:10],
                importance=importance,
                bucket=bucket,
                status="active",
                created_at=today,
                updated_at=today,
                source="auto_dream",
            )
            filepath = self.workspace.digest_path(bucket, name, agent_id=unit.get("agent_id"))
            write_markdown(filepath, fm, final_content)
            logger.info("auto_dream CREATE: %s → %s", name, filepath)

            # Update file catalog
            if self.file_catalog:
                self.file_catalog.upsert(str(filepath), bucket=bucket)

            return "created"

        # CORROBORATE / REFINE / CORRECT → update the top match
        top_match = active_nodes[0]
        top_path = Path(top_match.path)
        mem = read_markdown(top_path)
        if mem is None:
            return "skipped"

        # Check if archived — skip update
        if mem.frontmatter.status == "archived":
            logger.info("auto_dream: skipping archived node %s", name)
            # Create new instead
            fm = MemoryFrontmatter(
                name=name,
                description=summary,
                tags=tags[:10],
                importance=importance,
                bucket=bucket,
                status="active",
                created_at=today,
                updated_at=today,
                source="auto_dream",
            )
            filepath = self.workspace.digest_path(bucket, name, agent_id=unit.get("agent_id"))
            write_markdown(filepath, fm, final_content)
            if self.file_catalog:
                self.file_catalog.upsert(str(filepath), bucket=bucket)
            return "created"

        # Merge content
        updated_content = final_content
        if action in ("CORROBORATE", "REFINE"):
            update_count = mem.body.count("## Update (")
            if update_count >= 3:
                logger.info(
                    "auto_dream %s: skipping append — %s already has %d updates (max 3)",
                    action, name, update_count,
                )
                updated_content = mem.body
            elif final_content not in mem.body:
                updated_content = mem.body.rstrip() + f"\n\n---\n## Update ({today})\n\n{final_content}"
            else:
                logger.info("auto_dream %s: content duplicate in %s, skipping append", action, name)
                updated_content = mem.body

        # Add wikilink from existing to new name
        if name != mem.frontmatter.name:
            updated_content = add_wikilink(updated_content, name, predicate="relates_to")

        merged_tags = list(dict.fromkeys(mem.frontmatter.tags + tags))[:10]
        mem.frontmatter.importance = max(mem.frontmatter.importance, importance)
        mem.frontmatter.tags = merged_tags
        mem.frontmatter.updated_at = today

        write_markdown(top_path, mem.frontmatter, updated_content)
        logger.info("auto_dream %s: %s → %s", action, name, top_path)

        # Update file catalog
        if self.file_catalog:
            self.file_catalog.upsert(str(top_path), bucket=bucket)

        return action.lower()

    async def _dream_topics(
        self,
        units: list[dict],
        topic_candidates: list[dict],
    ) -> int:
        """Select top-N interest topics via LLM with fallback to rule-based."""
        # Build candidate list from topic_candidates or units
        candidates: list[dict] = []
        for tc in topic_candidates:
            if isinstance(tc, dict):
                candidates.append({
                    "title": str(tc.get("title", "")).strip(),
                    "reason": str(tc.get("reason", "")).strip(),
                    "keywords": [str(k) for k in (tc.get("keywords") or []) if k],
                    "evidence": str(tc.get("evidence", "")).strip(),
                })
        # Fallback: generate from units if no topic_candidates
        if not candidates:
            for unit in units:
                unit_topics = unit.get("topics") or []
                if isinstance(unit_topics, list) and unit_topics:
                    for t in unit_topics:
                        candidates.append({
                            "title": str(t),
                            "reason": unit.get("summary", ""),
                            "keywords": unit.get("tags", []),
                            "evidence": unit.get("name", ""),
                        })
                elif unit.get("name"):
                    candidates.append({
                        "title": unit.get("name", ""),
                        "reason": unit.get("summary", ""),
                        "keywords": unit.get("tags", []),
                        "evidence": unit.get("name", ""),
                    })

        if not candidates:
            logger.warning("auto_dream topics: no candidates collected")
            return 0

        # Collect recent 7-day topics for dedup
        today = date.today()
        recent_titles: set[str] = set()
        recent_topics: list[dict] = []
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
                                    # Store normalized form for dedup
                                    normalized = _normalize_topic_title(t)
                                    recent_titles.add(normalized)
                                    recent_topics.append(item)
                except Exception:
                    pass

        # Collect same-day existing topics (preserve, don't overwrite)
        same_day_path = self.workspace.interests_path()
        same_day_existing: list[dict] = []
        if same_day_path.exists():
            try:
                data = yaml.safe_load(same_day_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    same_day_existing = [d for d in data if isinstance(d, dict)]
            except Exception:
                pass

        # Try LLM selection
        top_topics = await self._llm_topic_selection(
            candidates, same_day_existing, recent_titles,
        )

        # Fallback to rule-based if LLM fails
        if top_topics is None:
            logger.info("auto_dream topics: LLM unavailable, using rule-based fallback")
            fresh = []
            for c in candidates:
                title = c.get("title", "")
                normalized = _normalize_topic_title(title)
                if normalized not in recent_titles:
                    fresh.append(c)
            top_topics = fresh[:3]

        if not top_topics:
            logger.info("auto_dream topics: no topics after selection, skipping")
            return 0

        # Preserve same-day existing + add new selections
        final_topics = list(same_day_existing)
        existing_titles = {_normalize_topic_title(t.get("title", "")) for t in same_day_existing if t.get("title")}
        for t in top_topics:
            title = t.get("title", "")
            if title:
                normalized = _normalize_topic_title(title)
                if normalized not in existing_titles:
                    final_topics.append(t)
                    existing_titles.add(normalized)

        # Write today's interests.yaml
        ipath = self.workspace.interests_path()
        ipath.parent.mkdir(parents=True, exist_ok=True)
        with open(ipath, "w", encoding="utf-8") as f:
            yaml.dump(final_topics, f, allow_unicode=True, default_flow_style=False)
        logger.info("auto_dream: wrote %d topics to %s", len(final_topics), ipath)

        return len(final_topics)

    async def _llm_topic_selection(
        self,
        candidates: list[dict],
        same_day_existing: list[dict],
        recent_titles: set[str],
    ) -> list[dict] | None:
        """Use LLM to select top topics. Returns None if LLM unavailable."""
        if not self._generate_fn:
            return None

        prompt_parts = [
            "## Candidate Topics\n",
            json.dumps(candidates, ensure_ascii=False, indent=2),
            "\n\n## Same-day Existing Topics (preserve these)\n",
            json.dumps(
                [{"title": t.get("title", ""), "reason": t.get("reason", "")} for t in same_day_existing],
                ensure_ascii=False, indent=2,
            ),
            "\n\n## Recent 7-day Topic Titles (avoid duplicates)\n",
            ", ".join(sorted(recent_titles)) if recent_titles else "(none)",
            "\n\nSelect up to 3 best topics. Return JSON with 'topics' array.",
        ]
        prompt = "\n".join(prompt_parts)

        try:
            raw = await asyncio.to_thread(self._generate_fn, _TOPICS_SYSTEM_PROMPT, prompt)
        except Exception as e:
            logger.warning("auto_dream topics LLM call failed: %s", e)
            return None

        raw = _strip_code_fence(raw)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("auto_dream topics: LLM output not valid JSON: %s", raw[:200])
            return None

        if not isinstance(parsed, dict):
            return None

        topics = parsed.get("topics", [])
        if not isinstance(topics, list):
            return None

        result: list[dict] = []
        for t in topics:
            if isinstance(t, dict):
                result.append({
                    "title": str(t.get("title", "")).strip(),
                    "reason": str(t.get("reason", "")).strip(),
                    "keywords": [str(k) for k in (t.get("keywords") or []) if k],
                    "evidence": str(t.get("evidence", "")).strip(),
                })

        return result[:3]

    def should_trigger(self, threshold: int) -> bool:
        """Check if auto_dream should trigger based on daily card count."""
        return self.workspace.count_unprocessed_daily_cards() >= threshold
