"""auto_memory — extract facts from conversations into daily cards.

After a conversation run ends, uses a single LLM call to extract key
facts (decisions, state, reusable procedures) and writes them as a
daily card under daily/<date>/<session_event>.md. Skips preference-
type facts (name, hobbies, location) — those are handled by the
separate PG-backed Preference system. Also writes session/ jsonl
dual-write (from PG Message).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import date

from app.memory.file_store.frontmatter import MemoryFrontmatter
from app.memory.file_store.markdown_io import read_markdown, write_markdown
from app.memory.file_store.workspace import MemoryWorkspace

logger = logging.getLogger(__name__)

_AUTO_MEMORY_SYSTEM_PROMPT = """\
You are a Memory Extractor for a multi-agent collaboration platform.
Extract key facts from the following conversation that are worth remembering
for future sessions.

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

## Output Format
Return a JSON object with a "facts" array. Each fact has "text", "tags", and "importance" (0-1):

{"facts": [
  {"text": "...", "tags": ["..."], "importance": 0.5}
]}

If nothing worth remembering, return: {"facts": []}

Match the language of the input conversation.
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


class AutoMemory:
    """Extract facts from conversations and write daily memory cards."""

    def __init__(self, workspace: MemoryWorkspace):
        self.workspace = workspace
        self._generate_fn: Callable | None = None

    def set_generate_fn(self, fn: Callable) -> None:
        self._generate_fn = fn

    async def run(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        conversation_id: str = "",
        agent_id: str = "",
    ) -> int:
        """Extract facts from a conversation and write daily card.

        Returns the number of facts extracted.
        """
        if not self._generate_fn:
            return 0
        if not user_msg and not assistant_msg:
            return 0
        if _is_trivial(user_msg, assistant_msg):
            return 0

        # Build conversation transcript
        messages = []
        if user_msg:
            messages.append({"role": "user", "content": user_msg})
        if assistant_msg:
            messages.append({"role": "assistant", "content": assistant_msg})

        prompt = "## Conversation\n" + json.dumps(messages, ensure_ascii=False)

        try:
            raw = await asyncio.to_thread(self._generate_fn, _AUTO_MEMORY_SYSTEM_PROMPT, prompt)
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

        facts = parsed.get("facts", [])
        if not isinstance(facts, list) or not facts:
            return 0

        # Write daily card
        today = date.today().isoformat()
        card_name = f"session_{conversation_id[:8]}" if conversation_id else f"session_{date.today().strftime('%H%M%S')}"
        filepath = self.workspace.daily_file_path(card_name, today)

        body_parts: list[str] = []
        tags_all: list[str] = []
        max_importance = 0.3
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            text = str(fact.get("text", "")).strip()
            if not text:
                continue
            body_parts.append(f"- {text}")
            tags = fact.get("tags", [])
            if isinstance(tags, list):
                tags_all.extend(str(t) for t in tags if t)
            imp = float(fact.get("importance", 0.5) or 0.5)
            max_importance = max(max_importance, imp)

        if not body_parts:
            return 0

        # Deduplicate tags
        seen_tags: set[str] = set()
        unique_tags = [t for t in tags_all if not (t in seen_tags or seen_tags.add(t))]

        # Check if daily card already exists (same conversation, multiple rounds)
        existing = read_markdown(filepath)
        if existing is not None:
            # Extract existing fact lines, preserving order
            existing_facts: list[str] = []
            existing_set: set[str] = set()
            for line in existing.body.split("\n"):
                stripped = line.strip()
                if (
                    stripped.startswith("- ")
                    and "## Conversation Summary" not in stripped
                    and not stripped.startswith("- *Source")
                    and stripped not in existing_set
                ):
                    existing_facts.append(stripped)
                    existing_set.add(stripped)

            # Append new facts that aren't duplicates
            appended = 0
            for fact_line in body_parts:
                if fact_line not in existing_set:
                    existing_facts.append(fact_line)
                    existing_set.add(fact_line)
                    appended += 1

            if appended == 0:
                logger.info("auto_memory: all %d facts already in %s, skip", len(body_parts), filepath)
                return 0

            # Merge tags and importance into existing frontmatter
            merged_tags = list(dict.fromkeys(
                existing.frontmatter.tags + unique_tags
            ))[:10]
            merged_importance = max(existing.frontmatter.importance, max_importance)

            fm = existing.frontmatter
            fm.tags = merged_tags
            fm.importance = merged_importance
            fm.updated_at = today
            if agent_id and not fm.agent_id:
                fm.agent_id = agent_id

            body = "## Conversation Summary\n\n" + "\n".join(existing_facts)
            if conversation_id:
                body += f"\n\n---\n*Source: session/{conversation_id}.jsonl*"

            write_markdown(filepath, fm, body)
            logger.info(
                "auto_memory: appended %d facts to %s (total: %d)",
                appended, filepath, len(existing_facts),
            )
            return appended

        # New daily card
        fm = MemoryFrontmatter(
            name=card_name,
            description=f"Memory from conversation {conversation_id[:8]}" if conversation_id else "Memory card",
            agent_id=agent_id or None,
            tags=unique_tags[:10],
            importance=max_importance,
            bucket="procedure",
            created_at=today,
            updated_at=today,
            source=f"session/{conversation_id}.jsonl" if conversation_id else "",
        )

        body = "## Conversation Summary\n\n" + "\n".join(body_parts)
        if conversation_id:
            body += f"\n\n---\n*Source: session/{conversation_id}.jsonl*"

        write_markdown(filepath, fm, body)
        logger.info("auto_memory: wrote %d facts to %s", len(body_parts), filepath)

        # Caller (MemoryService) handles indexing after run returns
        return len(body_parts)

    async def write_session_jsonl(
        self,
        conversation_id: str,
        messages: list[dict],
    ) -> None:
        """Write conversation messages to session/<conv_id>.jsonl (dual-write)."""
        if not conversation_id or not messages:
            return
        filepath = self.workspace.session_path(conversation_id)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "a", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
