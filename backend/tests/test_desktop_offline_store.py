"""Offline SQLite outbox happy path."""

from __future__ import annotations

from app.desktop.offline_store import OfflineStore
from app.desktop.sync import flush_outbox


class _FakeResp:
    def __init__(self, status_code: int = 200, text: str = "ok"):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.calls: list[tuple] = []

    async def request(self, method, path, json=None, params=None):
        self.calls.append((method, path, json))
        return _FakeResp(self.status_code)


def test_enqueue_and_flush_success(tmp_path):
    store = OfflineStore(tmp_path / "offline.db")
    item_id = store.enqueue(
        "message.create",
        {"content": "hello", "conversationId": "c1"},
        conversation_id="c1",
    )
    assert item_id
    pending = store.list_pending()
    assert len(pending) == 1

    client = _FakeClient(200)

    import asyncio

    report = asyncio.run(flush_outbox(store, client))  # type: ignore[arg-type]
    assert report.uploaded == 1
    assert report.failed == 0
    assert store.list_pending() == []
    assert client.calls[0][0] == "POST"
    assert client.calls[0][1] == "/api/sync/messages"


def test_conflict_not_silent(tmp_path):
    store = OfflineStore(tmp_path / "offline.db")
    store.enqueue("message.create", {"content": "x"}, conversation_id="c1")
    client = _FakeClient(409)

    import asyncio

    report = asyncio.run(flush_outbox(store, client))  # type: ignore[arg-type]
    assert report.conflicts == 1
    conflicts = store.list_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].last_error


def test_data_layout_paths(tmp_path):
    from app.desktop.runtime import DesktopRuntime

    rt = DesktopRuntime(data_dir=tmp_path / "AChat")
    rt.ensure_layout()
    store = OfflineStore(rt.sqlite_path())
    store.cache_message("m1", "c1", "user", {"parts": []})
    assert rt.sqlite_path().is_file()
