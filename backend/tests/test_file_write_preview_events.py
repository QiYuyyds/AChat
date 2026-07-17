"""Unit tests for FileWritePreviewCompleteEvent serialization."""

from app.schemas.events import FileWritePreviewCompleteEvent


def test_camelcase_aliasing():
    """Fields use camelCase aliases for frontend compatibility."""
    ev = FileWritePreviewCompleteEvent(
        conversationId="conv_1",
        timestamp=1000,
        messageId="msg_1",
        callId="call_1",
        path="src/app.tsx",
        oldContent="old",
        newContent="new",
        status="complete",
    )
    dumped = ev.model_dump(by_alias=True)
    assert dumped["conversationId"] == "conv_1"
    assert dumped["messageId"] == "msg_1"
    assert dumped["callId"] == "call_1"
    assert dumped["oldContent"] == "old"
    assert dumped["newContent"] == "new"
    assert dumped["type"] == "file_write_preview.complete"


def test_round_trip():
    """Serialize → deserialize round-trip preserves data."""
    ev = FileWritePreviewCompleteEvent(
        conversationId="conv_1",
        timestamp=1000,
        messageId="msg_1",
        callId="call_1",
        path="README.md",
        oldContent=None,
        newContent="# Hello",
        status="complete",
    )
    json_str = ev.model_dump_json(by_alias=True)
    restored = FileWritePreviewCompleteEvent.model_validate_json(json_str)
    assert restored.path == "README.md"
    assert restored.old_content is None
    assert restored.new_content == "# Hello"
    assert restored.status == "complete"


def test_failed_status():
    """Failed status with null diff data."""
    ev = FileWritePreviewCompleteEvent(
        conversationId="conv_1",
        timestamp=1000,
        messageId="msg_1",
        callId="call_1",
        path="failed.ts",
        oldContent=None,
        newContent=None,
        status="failed",
    )
    assert ev.status == "failed"
    assert ev.old_content is None
    assert ev.new_content is None
