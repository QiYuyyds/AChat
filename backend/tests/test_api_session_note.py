"""Tests for GET /api/conversations/{id}/session-note endpoint.

Covers:
  - success: valid YAML session note is returned as JSON
  - not found: no session note → { note: null, coversUpTo: null }
  - invalid YAML: plain-text summary → { note: null, coversUpTo: ... }
"""

from __future__ import annotations

from app.db.engine import get_db
from app.db.models import ContextSummary
from app.services import conversation_service as cs
from app.utils.clock import now_ms

_VALID_YAML = """\
title: Test Session
current_state: Working on API
key_decisions:
  - decided to use FastAPI
files_touched:
  - app.py (已读, 100 行)
commands_run:
  - pytest
artifacts_produced:
  - output.txt
blockers:
  - none
open_questions:
  - how to deploy
next_steps:
  - write tests
architecture_understanding: |
  Layered architecture
covers_up_to: 1234567890.0
"""


async def _seed_session_note(
    conv_id: str,
    summary: str,
    covers_up_to: float | None = 1234567890.0,
) -> None:
    async with get_db() as session:
        row = ContextSummary(
            id="cs_test_1",
            conversation_id=conv_id,
            summary=summary,
            covered_until_message_id="session",
            covered_until_created_at=int(covers_up_to or 0),
            source_message_count=5,
            token_estimate=100,
            model_provider=None,
            model_id=None,
            summary_type="session",
            covers_up_to=covers_up_to,
            created_at=now_ms(),
        )
        session.add(row)


async def test_api_get_session_note_success(api_client, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    await _seed_session_note(conv.id, _VALID_YAML)

    resp = await api_client.get(f"/api/conversations/{conv.id}/session-note")
    assert resp.status_code == 200
    body = resp.json()

    assert body["note"] is not None
    assert body["note"]["title"] == "Test Session"
    assert body["note"]["currentState"] == "Working on API"
    assert body["note"]["keyDecisions"] == ["decided to use FastAPI"]
    assert body["note"]["filesTouched"] == ["app.py (已读, 100 行)"]
    assert body["note"]["commandsRun"] == ["pytest"]
    assert body["note"]["artifactsProduced"] == ["output.txt"]
    assert body["note"]["blockers"] == ["none"]
    assert body["note"]["openQuestions"] == ["how to deploy"]
    assert body["note"]["nextSteps"] == ["write tests"]
    assert "Layered" in body["note"]["architectureUnderstanding"]
    assert body["coversUpTo"] == 1234567890.0


async def test_api_get_session_note_not_found(api_client, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])

    resp = await api_client.get(f"/api/conversations/{conv.id}/session-note")
    assert resp.status_code == 200
    body = resp.json()
    assert body["note"] is None
    assert body["coversUpTo"] is None


async def test_api_get_session_note_invalid_yaml(api_client, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    await _seed_session_note(conv.id, "this is plain text, not YAML")

    resp = await api_client.get(f"/api/conversations/{conv.id}/session-note")
    assert resp.status_code == 200
    body = resp.json()
    assert body["note"] is None
    assert body["coversUpTo"] is not None
