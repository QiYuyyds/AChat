"""Tests for the memory_read agent tool (app/tools/memory_store.py).

Covers:
- Path safety: directory traversal rejected, non-.md rejected, no FS leak
- Missing card: clean "not found" error instead of a stack trace
- Cross-agent readability ("link reach = authorization") with agent_id attribution
- Status annotation: superseded cards report their status
- Content truncation at MAX_READ_CONTENT_LENGTH with the truncated flag
- related adjacency reuse from build_expansion (same shape as memory_recall)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
async def memory_svc(db, tmp_path):
    """Create and initialize a real MemoryService with an isolated workspace."""
    from app.config import Settings
    from app.memory.memory_service import MemoryService

    settings = Settings(memory_workspace_dir=str(tmp_path / "memory"))
    svc = MemoryService(settings)
    await svc.initialize()
    with patch("app.main._memory_service", svc):
        yield svc


def _make_ctx(agent_id: str = "agent_caller"):
    from app.tools.base import ToolContext

    return ToolContext(
        conversation_id="conv_test",
        workspace_path="",
        agent_id=agent_id,
        run_id="run_test",
        cancel_event=asyncio.Event(),
    )


async def _read(path: str, agent_id: str = "agent_caller"):
    from app.tools.memory_store import memory_read_handler

    return await memory_read_handler({"path": path}, _make_ctx(agent_id))


def _write_card(svc, rel_parts: list[str], body: str, **fm_kwargs) -> Path:
    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import write_markdown

    filepath = svc.workspace.root.joinpath(*rel_parts)
    fm = MemoryFrontmatter(**fm_kwargs)
    write_markdown(filepath, fm, body)
    return filepath


# ─── Path safety ───────────────────────────────────────────────────────────


async def test_rejects_directory_traversal(memory_svc, tmp_path):
    """A path escaping the workspace root is refused and nothing is read."""
    outside = tmp_path / "outside.md"
    outside.write_text("secret payload", encoding="utf-8")

    result = await _read("../outside.md")

    assert not result.ok
    assert "escapes" in result.error
    assert "secret payload" not in (result.error or "")

    # Nested traversal that climbs out of the workspace root entirely
    result2 = await _read("digest/wiki/../../../outside.md")
    assert not result2.ok
    assert "escapes" in result2.error


async def test_rejects_non_md_path(memory_svc):
    """Non-.md paths are refused with a hint about the expected form."""
    for bad in ("digest/wiki/card.txt", "digest/wiki/card", "digest/wiki/card.md/"):
        result = await _read(bad)
        assert not result.ok
        assert ".md" in result.error


async def test_missing_card_clean_error(memory_svc):
    """A well-formed but nonexistent path returns a clean error, no stack."""
    result = await _read("digest/wiki/nope.md")

    assert not result.ok
    assert "not found" in result.error.lower()
    assert "nope.md" in result.error
    assert "Traceback" not in (result.error or "")


# ─── Read semantics ────────────────────────────────────────────────────────


async def test_cross_agent_card_readable_with_attribution(memory_svc):
    """Cards owned by another agent are readable and report their owner."""
    _write_card(
        memory_svc,
        ["digest", "wiki", "shared-node.md"],
        "Knowledge written by another agent",
        name="shared-node",
        agent_id="agent_owner",
    )

    result = await _read("digest/wiki/shared-node.md", agent_id="agent_intruder")

    assert result.ok
    assert result.value["agent_id"] == "agent_owner"
    assert result.value["content"] == "Knowledge written by another agent"


async def test_superseded_status_visible(memory_svc):
    """A superseded card reports its status so the agent can judge staleness."""
    _write_card(
        memory_svc,
        ["digest", "wiki", "old-truth.md"],
        "Outdated conclusion",
        name="old-truth",
        status="superseded",
    )

    result = await _read("digest/wiki/old-truth.md")

    assert result.ok
    assert result.value["status"] == "superseded"


async def test_content_truncated_over_limit(memory_svc):
    """Bodies over MAX_READ_CONTENT_LENGTH come back as a prefix + truncated flag."""
    from app.tools.memory_store import MAX_READ_CONTENT_LENGTH

    _write_card(
        memory_svc,
        ["digest", "wiki", "long-card.md"],
        "x" * (MAX_READ_CONTENT_LENGTH + 500),
        name="long-card",
    )

    result = await _read("digest/wiki/long-card.md")

    assert result.ok
    assert result.value["truncated"] is True
    assert len(result.value["content"]) == MAX_READ_CONTENT_LENGTH
    # Windows path uses backslashes; read the returned workspace-relative path
    assert result.value["path"].replace("\\", "/") == "digest/wiki/long-card.md"


async def test_short_content_not_truncated(memory_svc):
    _write_card(
        memory_svc,
        ["digest", "wiki", "short-card.md"],
        "short body",
        name="short-card",
    )

    result = await _read("digest/wiki/short-card.md")

    assert result.ok
    assert result.value["truncated"] is False
    assert result.value["content"] == "short body"


# ─── related adjacency ─────────────────────────────────────────────────────


async def test_related_reports_existing_neighbors_with_predicate(memory_svc):
    """related uses build_expansion: existing linked card appears with predicate,
    broken links are excluded."""
    _write_card(
        memory_svc,
        ["digest", "wiki", "node-a.md"],
        "Node A\nrelates_to:: [[digest/wiki/node-b.md]]\nrelates_to:: [[digest/wiki/ghost.md]]",
        name="node-a",
    )
    _write_card(
        memory_svc,
        ["digest", "wiki", "node-b.md"],
        "Node B",
        name="node-b",
    )
    memory_svc.auto_index.index_file(memory_svc.workspace.root / "digest" / "wiki" / "node-a.md")

    result = await _read("digest/wiki/node-a.md")

    assert result.ok
    outlinks = result.value["related"]["outlinks"]
    linked = [o for o in outlinks if "node-b" in o["path"].replace("\\", "/")]
    assert len(linked) == 1
    assert linked[0]["predicate"] == "relates_to"
    # Ghost target does not exist on disk → must not appear
    assert not any("ghost" in o["path"] for o in outlinks)
    # related shape matches memory_recall: path/name/predicate only
    assert set(linked[0].keys()) == {"path", "name", "predicate"}
