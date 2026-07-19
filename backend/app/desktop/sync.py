"""Outbox flush: primary DB / engine write path (v1); optional cloud client (legacy)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.desktop.offline_store import OfflineStore
from app.desktop.runtime import cloud_api_client_enabled

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    uploaded: int = 0
    conflicts: int = 0
    failed: int = 0
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


async def flush_outbox(
    store: OfflineStore,
    client: Any | None = None,
) -> SyncReport:
    """Apply pending outbox items to primary store (local engine DB) or legacy cloud API."""
    report = SyncReport()
    use_cloud = client is not None or cloud_api_client_enabled()

    for item in store.list_pending():
        try:
            if item.kind != "message.create":
                store.mark_failed(item.id, f"unknown kind {item.kind}")
                report.failed += 1
                report.errors.append(f"{item.id}: unknown kind {item.kind}")
                continue

            conv_id = item.conversation_id or item.payload.get("conversationId")
            if not conv_id:
                store.mark_failed(item.id, "missing conversationId")
                report.failed += 1
                report.errors.append(f"{item.id}: missing conversationId")
                continue

            if use_cloud and client is not None:
                ok, conflict, err = await _flush_via_cloud(client, item, str(conv_id))
            elif use_cloud:
                from app.desktop.cloud_client import get_cloud_client

                ok, conflict, err = await _flush_via_cloud(get_cloud_client(), item, str(conv_id))
            else:
                ok, conflict, err = await _flush_via_primary_db(item, str(conv_id))

            if conflict:
                store.mark_conflict(item.id, err or "conflict")
                report.conflicts += 1
                report.errors.append(f"{item.id}: {err or 'conflict'}")
            elif ok:
                store.mark_done(item.id)
                report.uploaded += 1
            else:
                store.mark_failed(item.id, err or "failed")
                report.failed += 1
                report.errors.append(f"{item.id}: {err or 'failed'}")
        except Exception as e:
            logger.warning("outbox upload failed id=%s: %s", item.id, e)
            store.mark_failed(item.id, str(e))
            report.failed += 1
            report.errors.append(f"{item.id}: {e}")
    return report


async def _flush_via_cloud(client: Any, item: Any, conv_id: str) -> tuple[bool, bool, str | None]:
    payload = dict(item.payload)
    payload.setdefault("conversationId", conv_id)
    resp = await client.request(
        "POST",
        "/api/sync/messages",
        json={"messages": [payload]},
    )
    if resp.status_code == 404:
        resp = await client.request(
            "POST",
            f"/api/conversations/{conv_id}/messages",
            json=item.payload,
        )
    if resp.status_code in (409, 412):
        return False, True, f"HTTP {resp.status_code}: {resp.text[:200]}"
    if resp.status_code >= 400:
        return False, False, f"HTTP {resp.status_code}"
    return True, False, None


async def _flush_via_primary_db(item: Any, conv_id: str) -> tuple[bool, bool, str | None]:
    """Write outbox message into primary PG (or engine DB) via SQLAlchemy models."""
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import Conversation, Message
    from app.utils.clock import now_ms

    payload = dict(item.payload)
    msg_id = str(payload.get("id") or item.id)
    role = str(payload.get("role") or "agent")
    parts = payload.get("parts")
    if parts is None:
        content = payload.get("content")
        parts = [{"type": "text", "content": content}] if content is not None else []
    status = str(payload.get("status") or "complete")
    now = int(payload.get("createdAt") or now_ms())

    try:
        async with get_db() as db:
            conv = await db.execute(select(Conversation).where(Conversation.id == conv_id))
            if conv.scalar_one_or_none() is None:
                return False, True, f"conversation {conv_id} missing on primary"

            existing = await db.execute(select(Message).where(Message.id == msg_id))
            if existing.scalar_one_or_none() is not None:
                # Already present — treat as success (idempotent), not silent overwrite of body.
                return True, False, None

            row = Message(
                id=msg_id,
                conversation_id=conv_id,
                role=role,
                parts=parts,
                status=status,
                created_at=now,
            )
            if payload.get("agentId") is not None:
                row.agent_id = payload.get("agentId")
            if payload.get("parentMessageId") is not None:
                row.parent_message_id = payload.get("parentMessageId")
            if payload.get("runId") is not None:
                row.run_id = payload.get("runId")
            if payload.get("hidden") is not None:
                row.hidden = bool(payload.get("hidden"))
            db.add(row)
            await db.flush()
        return True, False, None
    except Exception as e:
        logger.warning("primary DB outbox write failed: %s", e)
        return False, False, str(e)


def conflict_status_payload(store: OfflineStore) -> dict[str, Any]:
    items = store.list_conflicts()
    return {
        "conflictCount": len(items),
        "items": [
            {
                "id": i.id,
                "kind": i.kind,
                "conversationId": i.conversation_id,
                "error": i.last_error,
            }
            for i in items
        ],
    }
