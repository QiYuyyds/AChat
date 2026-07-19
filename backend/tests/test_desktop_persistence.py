"""Desktop online/offline message persistence helper."""

from __future__ import annotations

import asyncio

import httpx

from app.desktop.cloud_client import clear_cloud_session, set_cloud_access_token
from app.desktop.offline_store import OfflineStore
from app.desktop.persistence import persist_message_online_or_outbox
from app.desktop.runtime import DesktopRuntime, set_desktop_runtime
import app.desktop.runtime as runtime_mod


class _OkClient:
    base_url = "https://api.example"

    def _headers(self):
        return {"Authorization": "Bearer t"}

    async def post_message(self, conversation_id: str, body: dict):
        return {"ok": True, "conversationId": conversation_id, "id": body.get("id")}


class _ConflictClient(_OkClient):
    async def post_message(self, conversation_id: str, body: dict):
        req = httpx.Request("POST", "https://api.example/api/sync/messages")
        resp = httpx.Response(409, request=req, text="conflict")
        raise httpx.HTTPStatusError("conflict", request=req, response=resp)


def test_persist_online_success(tmp_path, monkeypatch):
    rt = DesktopRuntime(
        data_dir=tmp_path,
        engine_token="t",
        official_api_url="https://api.example",
        allowed_origins=["https://app.example"],
    )
    set_desktop_runtime(rt)
    set_cloud_access_token("jwt", user_id="u1")

    import app.desktop.persistence as pers

    monkeypatch.setattr(pers, "get_cloud_client", lambda: _OkClient())
    monkeypatch.setattr(pers, "cloud_reachable", lambda timeout=2.0: asyncio.sleep(0, result=True))

    async def _reachable(timeout=2.0):
        return True

    monkeypatch.setattr(pers, "cloud_reachable", _reachable)

    result = asyncio.run(
        persist_message_online_or_outbox(
            "c1",
            {"id": "m1", "conversationId": "c1", "role": "user", "parts": []},
            local_message_id="m1",
            role="user",
        )
    )
    assert result["mode"] == "cloud"
    assert result["conflict"] is False

    clear_cloud_session()
    runtime_mod._RUNTIME = None


def test_persist_offline_outbox(tmp_path, monkeypatch):
    rt = DesktopRuntime(
        data_dir=tmp_path,
        engine_token="t",
        official_api_url="https://api.example",
        allowed_origins=["https://app.example"],
    )
    set_desktop_runtime(rt)
    set_cloud_access_token("jwt", user_id="u1")

    import app.desktop.persistence as pers

    async def _unreachable(timeout=2.0):
        return False

    monkeypatch.setattr(pers, "cloud_reachable", _unreachable)

    result = asyncio.run(
        persist_message_online_or_outbox(
            "c1",
            {"id": "m2", "conversationId": "c1", "role": "user", "parts": []},
            local_message_id="m2",
            role="user",
        )
    )
    assert result["mode"] == "outbox"
    store = OfflineStore(rt.sqlite_path())
    assert len(store.list_pending()) == 1

    clear_cloud_session()
    runtime_mod._RUNTIME = None


def test_persist_conflict_marked(tmp_path, monkeypatch):
    rt = DesktopRuntime(
        data_dir=tmp_path,
        engine_token="t",
        official_api_url="https://api.example",
        allowed_origins=["https://app.example"],
    )
    set_desktop_runtime(rt)
    set_cloud_access_token("jwt", user_id="u1")

    import app.desktop.persistence as pers

    async def _reachable(timeout=2.0):
        return True

    monkeypatch.setattr(pers, "cloud_reachable", _reachable)
    monkeypatch.setattr(pers, "get_cloud_client", lambda: _ConflictClient())

    result = asyncio.run(
        persist_message_online_or_outbox(
            "c1",
            {"id": "m3", "conversationId": "c1", "role": "agent", "parts": []},
            local_message_id="m3",
            role="agent",
        )
    )
    assert result["conflict"] is True
    store = OfflineStore(rt.sqlite_path())
    assert len(store.list_conflicts()) == 1

    clear_cloud_session()
    runtime_mod._RUNTIME = None
