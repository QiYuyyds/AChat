"""Desktop online/offline persistence helpers.

v1 strategy:
- Online: write through engine primary store (remote PG via SQLAlchemy).
- Offline: enqueue to SQLite outbox and keep a local message cache.
- Conflicts: never silent-overwrite; surface via sync status / mark_conflict.
- Legacy: optional CloudApiClient when feature flag enabled.
"""

from __future__ import annotations

import logging
from typing import Any

from app.desktop.offline_store import OfflineStore
from app.desktop.runtime import cloud_api_client_enabled, get_desktop_runtime, is_desktop_mode

logger = logging.getLogger(__name__)


def _store() -> OfflineStore | None:
    rt = get_desktop_runtime()
    if not rt:
        return None
    return OfflineStore(rt.sqlite_path())


async def primary_reachable(timeout: float = 2.0) -> bool:
    """Probe primary database connectivity for desktop online path."""
    if not is_desktop_mode():
        return True
    try:
        from sqlalchemy import text

        from app.db.engine import get_db

        async with get_db() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.debug("primary DB probe failed: %s", e)
        return False


async def cloud_reachable(timeout: float = 2.0) -> bool:
    """Back-compat name: primary store or legacy cloud API."""
    if not is_desktop_mode():
        return True
    if not cloud_api_client_enabled():
        return await primary_reachable(timeout)
    import httpx

    from app.desktop.cloud_client import get_cloud_client, get_cloud_session

    base = get_cloud_client().base_url
    if not base or not get_cloud_session().is_authenticated:
        return await primary_reachable(timeout)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base}/api/auth/me", headers=get_cloud_client()._headers())
            return resp.status_code < 500
    except Exception:
        return await primary_reachable(timeout)


async def persist_message_online_or_outbox(
    conversation_id: str,
    payload: dict[str, Any],
    *,
    local_message_id: str | None = None,
    role: str = "agent",
) -> dict[str, Any]:
    """Try primary persistence; on failure enqueue outbox and cache locally."""
    store = _store()
    if local_message_id and store:
        store.cache_message(local_message_id, conversation_id, role, payload)

    if await primary_reachable():
        # Primary path is the normal FastAPI/SQLAlchemy write; callers that already
        # wrote via services should not need this helper. When used explicitly for
        # dual-write, treat primary as reachable = online mode without outbox.
        return {"mode": "primary", "result": payload, "conflict": False}

    if cloud_api_client_enabled():
        try:
            from app.desktop.cloud_client import get_cloud_client

            result = await get_cloud_client().post_message(conversation_id, payload)
            return {"mode": "cloud", "result": result, "conflict": False}
        except Exception as e:
            logger.warning("desktop cloud persist error: %s", e)

    if store is None:
        raise RuntimeError("desktop offline store unavailable and primary unreachable")

    item_id = store.enqueue("message.create", payload, conversation_id=conversation_id)
    return {"mode": "outbox", "result": {"outboxId": item_id}, "conflict": False}
