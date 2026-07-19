"""Desktop online/offline persistence helpers for messages and related metadata.

v1 strategy:
- Online: POST durable conversation messages through official cloud HTTP API.
- Offline: enqueue to SQLite outbox and keep a local message cache for continuation.
- Conflicts: never silent-overwrite; surface via sync status / mark_conflict.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.desktop.cloud_client import get_cloud_client, get_cloud_session
from app.desktop.offline_store import OfflineStore
from app.desktop.runtime import get_desktop_runtime, is_desktop_mode

logger = logging.getLogger(__name__)


def _store() -> OfflineStore | None:
    rt = get_desktop_runtime()
    if not rt:
        return None
    return OfflineStore(rt.sqlite_path())


async def cloud_reachable(timeout: float = 2.0) -> bool:
    if not is_desktop_mode():
        return True
    base = get_cloud_client().base_url
    if not base or not get_cloud_session().is_authenticated:
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Prefer a lightweight auth probe; fall back to any 2xx/401 meaning host is up.
            resp = await client.get(f"{base}/api/auth/me", headers=get_cloud_client()._headers())
            return resp.status_code < 500
    except Exception:
        return False


async def persist_message_online_or_outbox(
    conversation_id: str,
    payload: dict[str, Any],
    *,
    local_message_id: str | None = None,
    role: str = "agent",
) -> dict[str, Any]:
    """Try cloud persistence; on failure enqueue outbox and cache locally.

    Returns a status dict:
      { "mode": "cloud"|"outbox", "result": <cloud json or outbox id>, "conflict": bool }
    """
    store = _store()
    if local_message_id and store:
        store.cache_message(local_message_id, conversation_id, role, payload)

    if await cloud_reachable():
        try:
            result = await get_cloud_client().post_message(conversation_id, payload)
            return {"mode": "cloud", "result": result, "conflict": False}
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in (409, 412) and store:
                item_id = store.enqueue(
                    "message.create",
                    payload,
                    conversation_id=conversation_id,
                )
                store.mark_conflict(item_id, f"HTTP {status}")
                logger.warning(
                    "desktop persist conflict conversation=%s status=%s",
                    conversation_id,
                    status,
                )
                return {
                    "mode": "outbox",
                    "result": {"outboxId": item_id},
                    "conflict": True,
                }
            logger.warning("desktop cloud persist failed: %s", e)
        except Exception as e:
            logger.warning("desktop cloud persist error: %s", e)

    if store is None:
        raise RuntimeError("desktop offline store unavailable and cloud unreachable")

    item_id = store.enqueue("message.create", payload, conversation_id=conversation_id)
    return {"mode": "outbox", "result": {"outboxId": item_id}, "conflict": False}
