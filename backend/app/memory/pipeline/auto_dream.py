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

from app.memory.buckets import (
    make_stable_key,
    normalize_bucket,
    to_relative_memory_path,
    validate_and_normalize_unit,
)
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

## Bucket Classification (HARD BOUNDARIES)
- **procedure**: reusable how-to / runbooks / debugging strategies.
  Subject form: "When X, do steps…"
- **personal**: ONLY stable facts/rules ABOUT THE USER — identity, durable preferences,
  communication conventions, hard constraints/avoidances.
  Subject form: "User is… / User prefers… / Never…"
  personal body MUST be short: one Rule sentence + ≤5 How-to-apply bullets.
- **wiki**: reusable knowledge — concepts, works, geography, technical facts.
  Subject form: "X is… / X includes…"

## CRITICAL SPLIT RULE
If a card mixes user interest + encyclopedic detail, emit TWO units:
1) short personal interest/strategy card
2) wiki knowledge card
NEVER put long song lists, chapter stats, author bios, or project delivery logs into personal.

## NEVER put in personal
- project delivery status, deploy URLs, task board state, feature checklists
- how-to steps (use procedure)
- encyclopedias the user merely asked about (use wiki)
- atomic key=value prefs that need no why/how (handled by Preference store)

## Cross-File Merging
When multiple daily files teach the same abstraction, merge into one unit.

## SUPERSEDE Detection (Temporal Semantics)
When the user EXPLICITLY expresses a CHANGE of state/preference (present-tense
negation/replacement phrasing like "I don't like X anymore", "switched to Y"),
mark the unit with `"action": "SUPERSEDE"` and set the stable_key to the SAME
topic-level key as the card being replaced.

CRITICAL DISTINCTION:
- SUPERSEDE triggers ONLY on present-state negation/replacement: "不喜欢 X 了" / "改用 Y 了" / "no longer use X" / "switched to Y"
- Past-tense recollections ("我以前很喜欢 X" / "I used to love X") MUST NOT trigger SUPERSEDE → use CORROBORATE instead
- The old facts are preserved in the daily layer (provenance); only the digest card is rewritten to current state

In the unit output, include an "action" field:
- "CREATE" (default if no matching key)
- "CORROBORATE" | "REFINE" | "CORRECT" (existing actions)
- "SUPERSEDE" (explicit state change — rewrite current digest card)

## Output Format
Return JSON with a "units" array and a "topic_candidates" array:

{
  "units": [
    {
      "name": "concise title",
      "bucket": "procedure" or "personal" or "wiki",
      "stable_key": "user.identity.profile | user.interest.music.doudou | wiki.topic | proc.topic",
      "summary": "1-2 sentence summary",
      "content": "detailed Markdown body (personal: SHORT rule card only)",
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
- **SUPERSEDE**: User explicitly changed state/preference — rewrite the COMPLETE body to the CURRENT state. Old facts must NOT be carried over (history lives in daily layer). New `derived_from` pointing to the current daily card is REQUIRED. Importance takes the NEW unit's value (not max with old).

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
- **SUPERSEDE**: User explicitly changed state/preference — rewrite the COMPLETE body to the CURRENT state. Old facts must NOT be carried over (history lives in daily layer). New `derived_from` pointing to the current daily card is REQUIRED. Importance takes the NEW unit's value (not max with old).

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
You are a Memory Integration Engine for PERSONAL-type memories
(identity, durable preferences, conventions, constraints, avoidances ABOUT THE USER).

## Workflow
1. Recall existing personal nodes (same stable topic) and related wiki nodes
2. Classify action
3. Write SHORT rule card only
4. Weave wikilinks (relate to wiki knowledge; do not copy wiki body)

## Actions
- **CREATE**: No matching personal node — complete short body
- **CORROBORATE**: confirm existing — only NEW evidence/source to append
- **REFINE**: improve existing — only NEW details to append
- **CORRECT**: replace with complete corrected short body
- **SUPERSEDE**: User explicitly changed state/preference — rewrite the COMPLETE body to the CURRENT state. Old facts must NOT be carried over (history lives in daily layer via derived_from). New `derived_from` pointing to the current daily card is REQUIRED. Importance takes the NEW unit's value (not max with old).

## Body Shape (KEEP SHORT)
```
## Rule / fact
<one sentence: User is… / prefers… / never…>

## Why
<1-2 short reasons>

## How to apply
- ≤5 bullets, actionable, no encyclopedias
```
FORBIDDEN in personal body: song lists, chapter counts, author bios, deploy URLs,
feature checklists, long how-to steps.

If existing wiki nodes cover the knowledge side, add
`relates_to:: [[digest/wiki/...]]` instead of copying facts.

## Wikilink Rules
- `derived_from:: [[daily/<date>/<session>.md]]` (relative paths only)
- `relates_to:: [[digest/<bucket>/<name>.md]]` for related nodes
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

_MERGE_UPDATE_PROMPT = """\
You are a Memory Merge Engine. A digest memory card has accumulated multiple
"## Update (date)" sections. Your task is to integrate ALL update sections into
the main body, producing a single coherent updated document.

## Rules
1. Merge all update content into the main body — do not simply concatenate
2. Remove redundant or superseded information
3. Preserve the most current state as the primary content
4. Keep the update structure (wikilinks, derived_from) intact
5. After merging, the "## Update" sections should be removed (they are now in the body)

## Output Format
Return JSON:
{
  "merged_body": "<the complete merged markdown body with all updates integrated>"
}

If the updates cannot be meaningfully merged, return:
{
  "merged_body": "<original body with updates appended>"
}

Match the language of the input document.
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


def _path_key(path: str) -> str:
    """Normalize memory path strings for source_path matching."""
    return path.replace("\\", "/").lstrip("./").strip()


def _match_changed_path(path: str, changed_paths: list[str]) -> str | None:
    """Map an LLM-provided source path onto a known changed path, if any.

    Accepts exact / slash-normalized matches and unique basename matches.
    Does NOT invent sources from the batch when matching fails.
    """
    if not path or not changed_paths:
        return None
    key = _path_key(path)
    if not key:
        return None

    by_key = {_path_key(c): c for c in changed_paths}
    if key in by_key:
        return by_key[key]

    # Absolute or partially-qualified path ending with a changed relative path.
    for ck, original in by_key.items():
        if key.endswith("/" + ck) or ck.endswith("/" + key):
            return original

    # Basename fallback only when unique among changed files.
    base = key.rsplit("/", 1)[-1]
    basename_hits = [c for c in changed_paths if _path_key(c).rsplit("/", 1)[-1] == base]
    if len(basename_hits) == 1:
        return basename_hits[0]
    return None


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

        # Validate source_paths against changed files (normalized match).
        # Keep catalog/original path strings here; relativize only when writing
        # derived_from wikilinks. Never invent multi-file provenance from the batch.
        normalized_units: list[dict] = []
        for unit in units:
            if not isinstance(unit, dict):
                continue
            source_paths = unit.get("source_paths", [])
            if not isinstance(source_paths, list):
                source_paths = []
            valid_paths: list[str] = []
            seen: set[str] = set()
            for path in source_paths:
                matched = _match_changed_path(str(path), changed_paths)
                if matched and matched not in seen:
                    seen.add(matched)
                    valid_paths.append(matched)
            # Unique-source fallback: if LLM paths all missed but exactly one
            # daily changed, that file is the only possible provenance.
            if not valid_paths and len(changed_paths) == 1:
                valid_paths = [changed_paths[0]]
            elif not valid_paths and source_paths:
                logger.debug(
                    "auto_dream extract: no source_paths matched changed files for unit %s",
                    unit.get("name", ""),
                )
            unit["source_paths"] = valid_paths

            cleaned = validate_and_normalize_unit(unit)
            if cleaned is None:
                logger.info(
                    "auto_dream extract: dropped unit after validator: %s",
                    unit.get("name", ""),
                )
                continue
            normalized_units.append(cleaned)

        return normalized_units[:max_units], topic_candidates

    def _find_by_stable_key(self, stable_key: str, bucket: str) -> Path | None:
        """Locate an existing digest file with the same stable_key in-bucket."""
        if not stable_key:
            return None
        for f in self.workspace.list_digest_files(bucket=bucket):
            mem = read_markdown(f)
            if not mem or mem.frontmatter.status == "archived":
                continue
            key = (mem.frontmatter.stable_key or "").strip()
            if not key:
                key = make_stable_key(mem.frontmatter.bucket, mem.frontmatter.name)
            if key == stable_key:
                return f
        return None

    def _node_from_path(self, path: Path, score: float = 1.0):
        mem = read_markdown(path)
        if not mem:
            return None
        return type(
            "Node",
            (),
            {
                "path": str(path),
                "name": mem.frontmatter.name,
                "content": mem.body,
                "score": score,
                "frontmatter": mem.frontmatter.to_dict(),
                "bucket": mem.frontmatter.bucket,
            },
        )()

    def _collect_related_nodes(
        self,
        *,
        name: str,
        summary: str,
        bucket: str,
        stable_key: str,
    ) -> tuple[list, list]:
        """Return (same_bucket_nodes, cross_bucket_nodes)."""
        same: list = []
        cross: list = []
        seen_paths: set[str] = set()

        key_hit = self._find_by_stable_key(stable_key, bucket)
        if key_hit is not None:
            node = self._node_from_path(key_hit, score=1.0)
            if node is not None:
                same.append(node)
                seen_paths.add(str(key_hit.resolve()))

        hits: list = []
        if self.node_search:
            hits = _dedupe_by_path(
                self.node_search.search(query=name, bucket=None, limit=10),
                self.node_search.search(query=summary or name, bucket=None, limit=10),
            )

        for node in hits:
            fm = node.frontmatter if hasattr(node, "frontmatter") else {}
            if isinstance(fm, dict) and fm.get("status") == "archived":
                continue
            node_path = str(getattr(node, "path", ""))
            try:
                resolved = str(Path(node_path).resolve())
            except OSError:
                resolved = node_path
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)

            node_bucket = ""
            if isinstance(fm, dict):
                node_bucket = str(fm.get("bucket") or "")
            if not node_bucket:
                node_bucket = str(getattr(node, "bucket", "") or "")
            node_bucket = normalize_bucket(node_bucket) if node_bucket else bucket

            if node_bucket == bucket:
                same.append(node)
            else:
                cross.append(node)

        return same[:5], cross[:5]

    def _write_create(
        self,
        *,
        name: str,
        summary: str,
        tags: list[str],
        importance: float,
        bucket: str,
        stable_key: str,
        today: str,
        body: str,
        agent_id: str | None,
    ) -> str:
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
            stable_key=stable_key,
        )
        filepath = self.workspace.digest_path(bucket, name, agent_id=agent_id)
        write_markdown(filepath, fm, body)
        logger.info("auto_dream CREATE: %s → %s", name, filepath)
        if self.file_catalog:
            self.file_catalog.upsert(str(filepath), bucket=bucket)
        return "created"

    async def _write_update(
        self,
        *,
        top_path: Path,
        action: str,
        name: str,
        tags: list[str],
        importance: float,
        stable_key: str,
        today: str,
        final_content: str,
        bucket: str,
    ) -> str:
        mem = read_markdown(top_path)
        if mem is None:
            return "skipped"
        if mem.frontmatter.status == "archived":
            return "skipped"

        updated_content = final_content
        if action in ("CORROBORATE", "REFINE"):
            update_count = mem.body.count("## Update (")
            if update_count >= 3:
                # Merge path: call LLM to integrate all updates into the body
                merged = await self._try_merge_updates(mem.body, final_content, today)
                if merged:
                    updated_content = merged
                    logger.info(
                        "auto_dream %s: merged %d updates into body for %s",
                        action, update_count, name,
                    )
                else:
                    # Fallback: append as new update section (allow temporarily >3)
                    updated_content = (
                        mem.body.rstrip()
                        + f"\n\n---\n## Update ({today})\n\n{final_content}"
                    )
                    logger.info(
                        "auto_dream %s: merge failed, appended update to %s",
                        action, name,
                    )
            elif final_content not in mem.body:
                updated_content = (
                    mem.body.rstrip()
                    + f"\n\n---\n## Update ({today})\n\n{final_content}"
                )
            else:
                updated_content = mem.body

        if name != mem.frontmatter.name:
            updated_content = add_wikilink(
                updated_content, name, predicate="relates_to"
            )

        mem.frontmatter.importance = max(mem.frontmatter.importance, importance)
        mem.frontmatter.tags = list(dict.fromkeys(mem.frontmatter.tags + tags))[:10]
        mem.frontmatter.updated_at = today
        if not mem.frontmatter.stable_key:
            mem.frontmatter.stable_key = stable_key

        write_markdown(top_path, mem.frontmatter, updated_content)
        logger.info("auto_dream %s: %s → %s", action, name, top_path)
        if self.file_catalog:
            self.file_catalog.upsert(str(top_path), bucket=bucket)
        return action.lower()

    def _write_supersede(
        self,
        *,
        top_path: Path,
        name: str,
        tags: list[str],
        importance: float,
        stable_key: str,
        today: str,
        final_content: str,
        bucket: str,
        source_paths: list[str],
    ) -> str:
        """SUPERSEDE program path: rewrite existing card to current state.

        - Body is fully replaced with final_content (old facts NOT carried over)
        - derived_from is preserved and new link(s) appended
        - importance takes the NEW unit's value (NOT max with old)
        - updated_at is refreshed
        - status becomes 'superseded' is NOT set here — the card remains active
          but its content now reflects the current state
        """
        mem = read_markdown(top_path)
        if mem is None:
            return "skipped"
        if mem.frontmatter.status == "archived":
            return "skipped"

        # Build new derived_from links from source_paths
        new_derives = [
            f"derived_from:: [[{src}]]" for src in source_paths[:3] if src
        ]
        # Preserve existing derived_from links and add new ones
        existing_derives = [
            line for line in mem.body.splitlines()
            if line.strip().startswith("derived_from::")
        ]
        all_derives = list(dict.fromkeys(existing_derives + new_derives))

        # Ensure derived_from is in the final content
        content_lines = final_content.splitlines()
        # Remove any existing derived_from from final_content (we'll consolidate)
        body_lines = [
            line for line in content_lines if not line.strip().startswith("derived_from::")
        ]
        updated_content = "\n".join(body_lines).rstrip()
        if all_derives:
            updated_content += "\n" + "\n".join(all_derives)

        # Update frontmatter: importance takes NEW value (not max)
        mem.frontmatter.importance = importance
        mem.frontmatter.tags = list(dict.fromkeys(mem.frontmatter.tags + tags))[:10]
        mem.frontmatter.updated_at = today
        mem.frontmatter.stable_key = stable_key
        # Mark as superseded in status
        mem.frontmatter.status = "superseded"

        write_markdown(top_path, mem.frontmatter, updated_content)
        logger.info("auto_dream SUPERSEDE: %s → %s (importance=%.2f)", name, top_path, importance)
        if self.file_catalog:
            self.file_catalog.upsert(str(top_path), bucket=bucket)
        return "superseded"

    async def _try_merge_updates(
        self, current_body: str, new_content: str, today: str
    ) -> str | None:
        """Attempt to merge accumulated Update sections into the main body.

        Returns the merged body on success, None on failure (caller should
        fallback to appending).
        """
        if not self._generate_fn:
            return None
        prompt = (
            f"## Current Document Body\n{current_body}\n\n"
            f"## New Update Content\n{new_content}\n\n"
            "Merge all '## Update (date)' sections into the main body."
        )
        try:
            raw = await asyncio.to_thread(self._generate_fn, _MERGE_UPDATE_PROMPT, prompt)
        except Exception as e:
            logger.warning("auto_dream merge LLM call failed: %s", e)
            return None

        raw = _strip_code_fence(raw)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("auto_dream merge: LLM output not valid JSON: %s", raw[:200])
            return None

        if not isinstance(parsed, dict):
            return None

        merged = str(parsed.get("merged_body", "")).strip()
        if not merged:
            return None
        return merged

    async def _dream_integrate(self, unit: dict) -> str:
        """Integrate one unit: stable_key merge → per-bucket LLM → write."""
        name = str(unit.get("name", "")).strip()
        if not name:
            return "skipped"

        bucket = normalize_bucket(str(unit.get("bucket", "wiki")))
        summary = str(unit.get("summary", "")).strip()
        content = str(unit.get("content", "")).strip()
        tags = [str(t) for t in (unit.get("tags") or []) if t]
        importance = float(unit.get("importance", 0.5) or 0.5)
        root_str = str(self.workspace.root.resolve())
        source_paths = [
            to_relative_memory_path(str(p), workspace_root=root_str)
            for p in (unit.get("source_paths") or [])
        ]
        stable_key = str(unit.get("stable_key") or "").strip() or make_stable_key(
            bucket, name
        )
        today = date.today().isoformat()
        agent_id = unit.get("agent_id")

        same_nodes, cross_nodes = self._collect_related_nodes(
            name=name, summary=summary, bucket=bucket, stable_key=stable_key
        )
        # Deterministic merge target: stable_key path wins
        merge_path = self._find_by_stable_key(stable_key, bucket)

        if bucket == "procedure":
            system_prompt = _INTEGRATE_SYSTEM_PROMPT_PROCEDURE
        elif bucket == "personal":
            system_prompt = _INTEGRATE_SYSTEM_PROMPT_PERSONAL
        else:
            system_prompt = _INTEGRATE_SYSTEM_PROMPT_WIKI

        context_parts: list[str] = []
        for node in same_nodes:
            context_parts.append(
                f"### {getattr(node, 'name', '')}\n"
                f"Path: {getattr(node, 'path', '')}\nBucket: {bucket}\n"
                f"Content: {getattr(node, 'content', '')[:500]}"
            )
        for node in cross_nodes:
            nb = getattr(node, "bucket", "") or (
                node.frontmatter.get("bucket")
                if isinstance(getattr(node, "frontmatter", None), dict)
                else ""
            )
            context_parts.append(
                f"### [OTHER BUCKET] {getattr(node, 'name', '')}\n"
                f"Path: {getattr(node, 'path', '')}\nBucket: {nb}\n"
                f"Content: {getattr(node, 'content', '')[:300]}\n"
                f"(Do NOT copy body; add relates_to only)"
            )
        existing_context = "\n---\n".join(context_parts)

        provenance_links = [
            f"derived_from:: [[{src}]]" for src in source_paths[:3] if src
        ]
        relate_hints = []
        for node in cross_nodes[:3]:
            rel = to_relative_memory_path(
                str(getattr(node, "path", "")), workspace_root=root_str
            )
            if rel:
                relate_hints.append(f"relates_to:: [[{rel}]]")

        prompt = (
            f"## New Memory Unit\n"
            f"Name: {name}\nBucket: {bucket}\nStable key: {stable_key}\n"
            f"Summary: {summary}\nContent: {content}\n"
            f"Source paths: {', '.join(source_paths[:5])}\n"
            f"Suggested provenance: {chr(10).join(provenance_links)}\n"
        )
        if relate_hints:
            prompt += (
                f"Suggested relates_to (cross-bucket): {chr(10).join(relate_hints)}\n"
            )
        prompt += "\n"
        if existing_context:
            prompt += f"## Existing Memory Files\n{existing_context}\n\n"
        prompt += (
            "Decide the action and provide final content with wikilinks. "
            "Merge same-bucket/stable_key matches; never copy cross-bucket bodies."
        )

        try:
            raw = await asyncio.to_thread(self._generate_fn, system_prompt, prompt)
        except Exception as e:
            logger.warning("auto_dream integrate LLM call failed: %s", e)
            return "skipped"

        raw = _strip_code_fence(raw)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug(
                "auto_dream integrate: LLM output not valid JSON: %s", raw[:200]
            )
            return "skipped"
        if not isinstance(parsed, dict):
            return "skipped"

        action = str(parsed.get("action", "CREATE")).strip().upper()
        final_content = str(parsed.get("content", content)).strip() or content

        if "derived_from::" not in final_content:
            for link in provenance_links:
                if link not in final_content:
                    final_content = final_content.rstrip() + f"\n{link}"

        if bucket == "personal":
            for link in relate_hints:
                if link not in final_content:
                    final_content = final_content.rstrip() + f"\n{link}"

        # SUPERSEDE program path: rewrite existing card to current state
        if action == "SUPERSEDE" and merge_path is not None:
            result = self._write_supersede(
                top_path=merge_path,
                name=name,
                tags=tags,
                importance=importance,
                stable_key=stable_key,
                today=today,
                final_content=final_content,
                bucket=bucket,
                source_paths=source_paths,
            )
            if result != "skipped":
                return result

        # stable_key hit always merges, even if LLM says CREATE
        if merge_path is not None:
            if action == "CREATE":
                action = "REFINE"
            result = await self._write_update(
                top_path=merge_path,
                action=action,
                name=name,
                tags=tags,
                importance=importance,
                stable_key=stable_key,
                today=today,
                final_content=final_content,
                bucket=bucket,
            )
            if result != "skipped":
                return result

        if action != "CREATE" and same_nodes:
            top_path = Path(same_nodes[0].path)
            result = await self._write_update(
                top_path=top_path,
                action=action,
                name=name,
                tags=tags,
                importance=importance,
                stable_key=stable_key,
                today=today,
                final_content=final_content,
                bucket=bucket,
            )
            if result != "skipped":
                return result

        return self._write_create(
            name=name,
            summary=summary,
            tags=tags,
            importance=importance,
            bucket=bucket,
            stable_key=stable_key,
            today=today,
            body=final_content,
            agent_id=agent_id,
        )

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
        """Check if auto_dream should trigger based on pending daily card count.

        Uses count_changed (pending markers with mtime=0) instead of the
        old full-count approach, so only genuinely new/changed cards trigger.
        """
        if self.file_catalog is None:
            return False
        return self.file_catalog.count_changed(bucket="daily") >= threshold
