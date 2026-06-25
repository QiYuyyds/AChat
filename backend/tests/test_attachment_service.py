"""Tests for attachment_service upload / list / delete."""

import os

import pytest

from app.db.engine import get_db
from app.db.models import Conversation, Workspace
from app.services import attachment_service as svc
from app.utils.clock import now_ms


async def _seed_conversation(conv_id: str = "conv_att") -> str:
    """Create a conversation + sandbox workspace, return the conversation id."""
    from app.config import get_settings

    root = os.path.join(get_settings().workspace_root, conv_id)
    os.makedirs(root, exist_ok=True)
    now = now_ms()
    async with get_db() as db:
        conv = Conversation(
            id=conv_id,
            title="t",
            mode="single",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        db.add(conv)
        ws = Workspace(
            id=f"ws_{conv_id}",
            conversation_id=conv_id,
            mode="sandbox",
            root_path=root,
            created_at=now,
        )
        db.add(ws)
    return conv_id


async def test_upload_creates_row_and_file(db):
    conv_id = await _seed_conversation()
    row = await svc.upload_attachment(conv_id, "hello.png", b"\x89PNGdata")

    assert row.conversation_id == conv_id
    assert row.kind == "image"
    assert row.mime_type == "image/png"
    assert row.file_name == "hello.png"
    assert row.file_path == f"uploads/{row.id}.png"
    assert row.size == len(b"\x89PNGdata")

    abs_path = await svc.get_attachment_absolute_path(row.id)
    assert abs_path is not None
    assert os.path.isfile(abs_path)
    with open(abs_path, "rb") as f:
        assert f.read() == b"\x89PNGdata"


async def test_upload_non_image_is_file_kind(db):
    conv_id = await _seed_conversation()
    row = await svc.upload_attachment(conv_id, "notes.txt", b"text body")
    assert row.kind == "file"
    assert row.mime_type == "text/plain"


async def test_upload_uses_declared_content_type(db):
    conv_id = await _seed_conversation()
    row = await svc.upload_attachment(
        conv_id, "blob.bin", b"xx", content_type="image/webp"
    )
    assert row.kind == "image"
    assert row.mime_type == "image/webp"


async def test_upload_unknown_ext_keeps_ext_but_octet_stream_mime(db):
    conv_id = await _seed_conversation()
    # 8-char ext passes the .[a-z0-9]{1,8} sanitizer but has no known mime.
    row = await svc.upload_attachment(conv_id, "thing.weirdext", b"xx")
    assert row.mime_type == "application/octet-stream"
    assert row.kind == "file"
    assert row.file_path == f"uploads/{row.id}.weirdext"


async def test_upload_invalid_ext_is_dropped(db):
    conv_id = await _seed_conversation()
    # >8 chars / dotted name -> ext sanitized to empty string.
    row = await svc.upload_attachment(conv_id, "archive.tar.gzooooong", b"xx")
    assert row.mime_type == "application/octet-stream"
    assert row.file_path == f"uploads/{row.id}"


async def test_upload_rejects_empty(db):
    conv_id = await _seed_conversation()
    with pytest.raises(ValueError, match="Empty file"):
        await svc.upload_attachment(conv_id, "empty.txt", b"")


async def test_upload_rejects_oversized(db):
    conv_id = await _seed_conversation()
    big = b"a" * (svc.MAX_FILE_SIZE + 1)
    with pytest.raises(ValueError, match="too large"):
        await svc.upload_attachment(conv_id, "big.txt", big)


async def test_upload_unknown_conversation_raises(db):
    with pytest.raises(ValueError, match="Workspace not found"):
        await svc.upload_attachment("conv_missing", "x.txt", b"data")


async def test_list_attachments_newest_first(db):
    conv_id = await _seed_conversation()
    a = await svc.upload_attachment(conv_id, "a.txt", b"a")
    b = await svc.upload_attachment(conv_id, "b.txt", b"bb")

    rows = await svc.list_attachments(conv_id)
    ids = [r.id for r in rows]
    assert set(ids) == {a.id, b.id}
    # ordered by created_at desc; both ids present, list length correct
    assert len(rows) == 2


async def test_list_attachments_scoped_to_conversation(db):
    conv1 = await _seed_conversation("conv_one")
    conv2 = await _seed_conversation("conv_two")
    await svc.upload_attachment(conv1, "a.txt", b"a")

    assert len(await svc.list_attachments(conv1)) == 1
    assert len(await svc.list_attachments(conv2)) == 0


async def test_delete_removes_row_and_file(db):
    conv_id = await _seed_conversation()
    row = await svc.upload_attachment(conv_id, "gone.txt", b"data")
    abs_path = await svc.get_attachment_absolute_path(row.id)
    assert abs_path is not None and os.path.isfile(abs_path)

    await svc.delete_attachment(row.id)

    assert await svc.get_attachment(row.id) is None
    assert not os.path.exists(abs_path)


async def test_delete_missing_raises(db):
    await _seed_conversation()
    with pytest.raises(ValueError, match="Attachment not found"):
        await svc.delete_attachment("att_nope")


async def test_delete_tolerates_already_removed_file(db):
    conv_id = await _seed_conversation()
    row = await svc.upload_attachment(conv_id, "x.txt", b"data")
    abs_path = await svc.get_attachment_absolute_path(row.id)
    os.remove(abs_path)  # file gone, row remains

    # should not raise; row still deleted
    await svc.delete_attachment(row.id)
    assert await svc.get_attachment(row.id) is None
