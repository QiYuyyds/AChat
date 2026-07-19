"""Best-effort outbox upload on reconnect (v1, no silent overwrite)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.desktop.cloud_client import CloudApiClient, get_cloud_client
from app.desktop.offline_store import OfflineStore

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
    client: CloudApiClient | None = None,
) -> SyncReport:
    client = client or get_cloud_client()
    report = SyncReport()
    for item in store.list_pending():
        try:
            if item.kind == "message.create":
                conv_id = item.conversation_id or item.payload.get("conversationId")
                if not conv_id:
                    store.mark_failed(item.id, "missing conversationId")
                    report.failed += 1
                    report.errors.append(f"{item.id}: missing conversationId")
                    continue
                payload = dict(item.payload)
                payload.setdefault("conversationId", conv_id)
                resp = await client.request(
                    "POST",
                    "/api/sync/messages",
                    json={"messages": [payload]},
                )
                # Older cloud without sync endpoint: fall back carefully.
                if resp.status_code == 404:
                    resp = await client.request(
                        "POST",
                        f"/api/conversations/{conv_id}/messages",
                        json=item.payload,
                    )
                if resp.status_code in (409, 412):
                    store.mark_conflict(item.id, f"HTTP {resp.status_code}: {resp.text[:200]}")
                    report.conflicts += 1
                    report.errors.append(f"{item.id}: conflict HTTP {resp.status_code}")
                    continue
                if resp.status_code >= 400:
                    store.mark_failed(item.id, f"HTTP {resp.status_code}")
                    report.failed += 1
                    report.errors.append(f"{item.id}: HTTP {resp.status_code}")
                    continue
                store.mark_done(item.id)
                report.uploaded += 1
            else:
                # Unknown kinds: do not drop silently
                store.mark_failed(item.id, f"unknown kind {item.kind}")
                report.failed += 1
                report.errors.append(f"{item.id}: unknown kind {item.kind}")
        except Exception as e:
            logger.warning("outbox upload failed id=%s: %s", item.id, e)
            store.mark_failed(item.id, str(e))
            report.failed += 1
            report.errors.append(f"{item.id}: {e}")
    return report


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
