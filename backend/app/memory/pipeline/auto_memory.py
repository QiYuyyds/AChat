"""auto_memory — extract facts from conversations into daily cards.

After a conversation run ends, uses a single LLM call to extract key
facts (decisions, state, reusable procedures) and writes them as a
daily card under daily/<date>/<session_event>.md.

Key enhancements over original:
  - Structured LLM output: {action, name, description, body, tags, importance}
  - Two prompt paths: create (new card) vs update (merge into existing)
  - Merge rules: timeline append, state rewrite, semantic dedup
  - LLM-generated semantic file naming (not program-generated)
  - Session JSONL: message ID dedup + tool_result/base64 sanitization
  - FileCatalog integration: upsert after write
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import date

from app.memory.file_store.file_catalog import FileCatalog
from app.memory.file_store.frontmatter import MemoryFrontmatter
from app.memory.file_store.markdown_io import read_markdown, write_markdown
from app.memory.file_store.wikilinks import extract_wikilinks_detailed, retarget_wikilinks
from app.memory.file_store.workspace import MemoryWorkspace
from app.memory.search.wikilink_expander import WikilinkExpander

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a Memory Extractor for a multi-agent collaboration platform.
Your job is to extract key facts from conversations that are worth remembering
for future sessions, and write them as a structured daily memory card.

## What to Extract
- Decisions and their rationale
- Current state of work (what's done, what's pending)
- Reusable procedures or patterns discovered
- Technical context (tech stack, architecture decisions, constraints)
- Task outcomes and lessons learned

## What NOT to Extract
- User personal preferences (name, hobbies, location) — handled separately
- Greetings, small talk, filler
- Transient details with no reuse value
- Information derivable from code

## Frontmatter Rules
- name: concise semantic name (kebab-case, describes the session topic)
- description: one-line summary of what this card covers
- body: Markdown body with bullet points or sections
- tags: 3-5 retrieval keywords
- importance: 0.0-1.0 (how valuable for future sessions)

## Merge Rules (for update path)
When updating an existing card:
- Timeline events: append chronologically (new events go at the end)
- State descriptions: rewrite to reflect the latest state (replace old state)
- Semantic duplicates: skip (if the new fact conveys the same meaning as an existing one)
- Generate the COMPLETE merged body, not just the new parts

If the conversation is trivial (greetings, small talk only), return:
{"action": "skip"}

Match the language of the input conversation.
"""

_CREATE_PROMPT_TEMPLATE = """\
## Task
Create a new daily memory card from the following conversation.

## Skip Check
If the conversation contains only greetings, small talk, or trivial exchanges
with no reusable knowledge, return: {{"action": "skip"}}

## Output Format
Return JSON:
{{
  "action": "create",
  "name": "<concise-semantic-name>",
  "description": "<one-line summary>",
  "body": "<markdown body with extracted facts>",
  "tags": ["<tag1>", "<tag2>"],
  "importance": 0.5
}}

## Conversation
{conversation}
"""

_UPDATE_PROMPT_TEMPLATE = """\
## Task
Update the existing daily memory card with new facts from the following conversation.

## Merge Rules
- Timeline events: append chronologically (new events at the end)
- State descriptions: rewrite to reflect the latest state (replace old state)
- Semantic duplicates: skip (if new fact conveys same meaning as existing)
- Generate the COMPLETE merged body (existing + new, properly merged)

## Skip Check
If the new conversation contains only information already in the card, or
only greetings/small talk, return: {{"action": "skip"}}

## Output Format
Return JSON:
{{
  "action": "update",
  "name": "<updated-semantic-name>",
  "body": "<complete merged markdown body>",
  "tags": ["<updated-tags>"],
  "importance": 0.6
}}

## Existing Card Body
{existing_body}

## Conversation
{conversation}
"""


def _strip_code_fence(raw: str) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"^```json", "", raw)
    raw = re.sub(r"^```", "", raw)
    raw = re.sub(r"```$", "", raw)
    return raw.strip()


def _is_trivial(user_msg: str, assistant_msg: str) -> bool:
    """Quick check: skip if both messages are very short or trivial."""
    combined = (user_msg + " " + assistant_msg).strip()
    if len(combined) < 30:
        return True
    trivial_patterns = [
        r"^(好的|没问题|OK|ok|明白|了解|收到|嗯|是的)[。.！!]?\s*$",
    ]
    return any(re.match(p, combined) for p in trivial_patterns)


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use as a filename: kebab-case, max 50 chars."""
    name = name.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    if not name:
        name = "session"
    return name[:50]


def _append_hash_suffix(name: str, conversation_id: str) -> str:
    """Append a short hash suffix for uniqueness."""
    if conversation_id:
        h = hashlib.md5(conversation_id.encode()).hexdigest()[:6]
        return f"{name}-{h}"
    return name


_TIMESTAMP_ALIASES = ("time_created", "timestamp", "createdAt", "timeCreated", "created_time")


def _normalize_timestamp(msg: dict) -> dict:
    """Normalize timestamp aliases to created_at field.
    
    Maps timestamp aliases to unified created_at field.
    """
    if msg.get("created_at"):
        return msg
    
    for key in _TIMESTAMP_ALIASES:
        if msg.get(key):
            return {**msg, "created_at": msg[key]}
    
    return msg


def _sanitize_msg_for_save(msg: dict) -> dict | None:
    """Sanitize a message for session/ JSONL storage.

    Strips tool_result blocks and base64 data blocks to prevent
    retrieval artifacts from being mistaken for user context.

    Returns None if the message should be skipped entirely.
    """
    if not isinstance(msg, dict):
        return None

    content = msg.get("content", "")

    # Skip messages with no content
    if not content:
        return None

    # If content is a list (parts), filter out tool_result and base64
    if isinstance(content, list):
        cleaned_parts = []
        for part in content:
            if not isinstance(part, dict):
                cleaned_parts.append(part)
                continue
            part_type = part.get("type", "")
            # Skip tool_result parts
            if part_type in ("tool_result", "tool_use"):
                continue
            # Skip parts with base64 data
            part_content = str(part.get("content", ""))
            if "data:image" in part_content or "data:application" in part_content:
                continue
            if len(part_content) > 10000:
                part = {**part, "content": part_content[:10000] + "...[truncated]"}
            cleaned_parts.append(part)
        if not cleaned_parts:
            return None
        return {**msg, "content": cleaned_parts}

    # If content is a string, strip base64 blocks
    if isinstance(content, str):
        # Remove base64 data URI patterns
        content = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "[base64-data-removed]", content)
        # Remove overly long content (>50KB likely tool_result dump)
        if len(content) > 50000:
            content = content[:50000] + "...[truncated]"
        return {**msg, "content": content}

    return msg


class AutoMemory:
    """Extract facts from conversations and write daily memory cards."""

    def __init__(
        self,
        workspace: MemoryWorkspace,
        file_catalog: FileCatalog | None = None,
        wikilink_expander: WikilinkExpander | None = None,
    ):
        self.workspace = workspace
        self.file_catalog = file_catalog
        self.wikilink_expander = wikilink_expander
        self._generate_fn: Callable | None = None

    def set_generate_fn(self, fn: Callable) -> None:
        self._generate_fn = fn

    def set_file_catalog(self, catalog: FileCatalog) -> None:
        self.file_catalog = catalog

    async def run(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        conversation_id: str = "",
        agent_id: str = "",
    ) -> int:
        """Extract facts from a conversation and write/update daily card.

        Returns 1 if a card was created or updated, 0 if skipped.
        """
        if not self._generate_fn:
            return 0
        if not user_msg and not assistant_msg:
            return 0
        if _is_trivial(user_msg, assistant_msg):
            return 0

        today = date.today().isoformat()

        # Build conversation transcript
        messages = []
        if user_msg:
            messages.append({"role": "user", "content": user_msg})
        if assistant_msg:
            messages.append({"role": "assistant", "content": assistant_msg})
        conversation_text = json.dumps(messages, ensure_ascii=False)

        # Check if a daily card already exists for this conversation
        # Try to find existing card by conversation_id in source field
        existing_file = self._find_existing_card(conversation_id, today)

        if existing_file is not None:
            # Update path
            return await self._update_card(existing_file, conversation_text, conversation_id, agent_id, today)
        else:
            # Create path
            return await self._create_card(conversation_text, conversation_id, agent_id, today)

    def _find_existing_card(self, conversation_id: str, today: str) -> str | None:
        """Find an existing daily card for this conversation.

        Looks for cards with source matching session/<conv_id>.jsonl.
        Returns the filename (without .md) if found, None otherwise.
        """
        if not conversation_id:
            return None

        today_dir = self.workspace.daily_dir / today
        if not today_dir.exists():
            return None

        source_marker = f"session/{conversation_id}.jsonl"
        for f in sorted(today_dir.glob("*.md")):
            mem = read_markdown(f)
            if mem and source_marker in (mem.frontmatter.source or ""):
                return f.stem

        # Also check by old naming convention
        old_name = f"session_{conversation_id[:8]}"
        old_path = self.workspace.daily_file_path(old_name, today)
        if old_path.exists():
            return old_name

        return None

    async def _create_card(
        self,
        conversation_text: str,
        conversation_id: str,
        agent_id: str,
        today: str,
    ) -> int:
        """Create a new daily card via LLM create prompt."""
        prompt = _CREATE_PROMPT_TEMPLATE.format(conversation=conversation_text)

        try:
            raw = await asyncio.to_thread(self._generate_fn, _SYSTEM_PROMPT, prompt)
        except Exception as e:
            logger.warning("auto_memory LLM call failed: %s", e)
            return 0

        raw = _strip_code_fence(raw)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("auto_memory: LLM output not valid JSON: %s", raw[:200])
            return 0

        if not isinstance(parsed, dict):
            return 0

        action = parsed.get("action", "create")
        if action == "skip":
            logger.info("auto_memory: LLM returned skip for conversation %s", conversation_id[:8])
            return 0

        name = str(parsed.get("name", "")).strip()
        if not name:
            name = f"session_{conversation_id[:8]}" if conversation_id else f"session_{today}"

        body = str(parsed.get("body", "")).strip()
        if not body:
            return 0

        description = str(parsed.get("description", "")).strip()
        tags = [str(t) for t in (parsed.get("tags") or []) if t]
        importance = float(parsed.get("importance", 0.5) or 0.5)
        importance = max(0.0, min(1.0, importance))

        # Sanitize name for filename
        safe_name = _sanitize_name(name)
        safe_name = _append_hash_suffix(safe_name, conversation_id)
        filepath = self.workspace.daily_file_path(safe_name, today)

        fm = MemoryFrontmatter(
            name=name,
            description=description or (f"Memory from conversation {conversation_id[:8]}" if conversation_id else "Memory card"),
            agent_id=agent_id or None,
            tags=tags[:10],
            importance=importance,
            bucket="procedure",
            status="active",
            created_at=today,
            updated_at=today,
            source=f"session/{conversation_id}.jsonl" if conversation_id else "",
        )

        write_markdown(filepath, fm, body)
        logger.info("auto_memory: created daily card %s (name=%s)", filepath, name)

        # Update file catalog
        if self.file_catalog:
            self.file_catalog.upsert(str(filepath), bucket="daily")

        return 1

    async def _update_card(
        self,
        existing_name: str,
        conversation_text: str,
        conversation_id: str,
        agent_id: str,
        today: str,
    ) -> int:
        """Update an existing daily card via LLM update prompt."""
        filepath = self.workspace.daily_file_path(existing_name, today)
        existing = read_markdown(filepath)
        if existing is None:
            return 0

        prompt = _UPDATE_PROMPT_TEMPLATE.format(
            existing_body=existing.body,
            conversation=conversation_text,
        )

        try:
            raw = await asyncio.to_thread(self._generate_fn, _SYSTEM_PROMPT, prompt)
        except Exception as e:
            logger.warning("auto_memory LLM call failed: %s", e)
            return 0

        raw = _strip_code_fence(raw)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("auto_memory: LLM output not valid JSON: %s", raw[:200])
            return 0

        if not isinstance(parsed, dict):
            return 0

        action = parsed.get("action", "update")
        if action == "skip":
            logger.info("auto_memory: LLM returned skip for update of %s", existing_name)
            return 0

        body = str(parsed.get("body", "")).strip()
        if not body:
            return 0

        # Update frontmatter
        fm = existing.frontmatter
        fm.updated_at = today
        if agent_id and not fm.agent_id:
            fm.agent_id = agent_id

        # Merge tags
        new_tags = [str(t) for t in (parsed.get("tags") or []) if t]
        merged_tags = list(dict.fromkeys(fm.tags + new_tags))[:10]
        fm.tags = merged_tags

        # Update importance
        new_importance = float(parsed.get("importance", 0.5) or 0.5)
        new_importance = max(0.0, min(1.0, new_importance))
        fm.importance = max(fm.importance, new_importance)

        # Optionally update name (but check if safe name actually differs from existing)
        new_name = str(parsed.get("name", "")).strip()
        safe_new_name = _sanitize_name(new_name) if new_name else existing_name
        
        # Check if file renaming is needed (only if sanitized name is different)
        if new_name and safe_new_name != existing_name:
            # File rename required - implement retarget process
            new_filepath = self.workspace.daily_file_path(safe_new_name, today)
            
            # Calculate relative paths for retargeting
            old_rel = filepath.relative_to(self.workspace.root)
            new_rel = new_filepath.relative_to(self.workspace.root)
            
            # Write new file
            write_markdown(new_filepath, fm, body)
            
            # Delete old file
            try:
                filepath.unlink()
                logger.info("auto_memory: deleted old file %s", filepath)
            except Exception as e:
                logger.warning("auto_memory: failed to delete old file %s: %s", filepath, e)
            
            # Retarget wikilinks in all .md files
            md_files = []
            if self.workspace.daily_dir.exists():
                md_files.extend(self.workspace.daily_dir.rglob("*.md"))
            if self.workspace.digest_dir.exists():
                md_files.extend(self.workspace.digest_dir.rglob("*.md"))
            
            for f in md_files:
                if f in (filepath, new_filepath):  # Skip old and new files
                    continue
                try:
                    content = f.read_text(encoding="utf-8")
                    if str(old_rel) in content:
                        new_content = retarget_wikilinks(content, str(old_rel), str(new_rel))
                        f.write_text(new_content, encoding="utf-8")
                        logger.debug("auto_memory: retargeted wikilinks in %s", f)
                except Exception as e:
                    logger.warning("auto_memory: failed to retarget wikilinks in %s: %s", f, e)
            
            # Update wikilink expander graph: remove old edges, rebuild for new path
            if self.wikilink_expander:
                self.wikilink_expander.remove_all_for(str(old_rel))
                links = [
                    (link.target, link.predicate)
                    for link in extract_wikilinks_detailed(body)
                ]
                if links:
                    self.wikilink_expander.add_edges_detailed(str(new_rel), links)

            # Update file catalog
            if self.file_catalog:
                self.file_catalog.remove(str(filepath))
                self.file_catalog.upsert(str(new_filepath), bucket="daily")
            
            logger.info("auto_memory: renamed and retargeted %s → %s", filepath, new_filepath)
        else:
            # Update frontmatter name if different
            if new_name and new_name != fm.name:
                fm.name = new_name
            
            write_markdown(filepath, fm, body)
            logger.info("auto_memory: updated daily card %s", filepath)

            # Update file catalog
            if self.file_catalog:
                self.file_catalog.upsert(str(filepath), bucket="daily")

        return 1

    async def write_session_jsonl(
        self,
        conversation_id: str,
        messages: list[dict],
    ) -> None:
        """Write conversation messages to session/<conv_id>.jsonl (dual-write).

        Messages are sanitized (tool_result/base64 stripped) and deduplicated
        by message ID (append-only with ID-based skip).
        """
        if not conversation_id or not messages:
            return

        filepath = self.workspace.session_path(conversation_id)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Read existing message IDs for dedup
        existing_ids: set[str] = set()
        if filepath.exists():
            try:
                with open(filepath, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            existing_msg = json.loads(line)
                            msg_id = existing_msg.get("id", "")
                            if msg_id:
                                existing_ids.add(msg_id)
                        except (json.JSONDecodeError, ValueError):
                            pass
            except Exception:
                pass

        # Write new messages (sanitized + deduplicated)
        with open(filepath, "a", encoding="utf-8") as f:
            for msg in messages:
                # Check for dedup by message ID
                msg_id = msg.get("id", "")
                if msg_id and msg_id in existing_ids:
                    continue

                # Normalize timestamp before sanitization
                normalized_msg = _normalize_timestamp(msg)
                
                sanitized = _sanitize_msg_for_save(normalized_msg)
                if sanitized is None:
                    continue

                f.write(json.dumps(sanitized, ensure_ascii=False) + "\n")

                if msg_id:
                    existing_ids.add(msg_id)
