import json

from pydantic import TypeAdapter


def test_progress_transport_is_rest_and_stream_contract_is_unchanged() -> None:
    from app.code_intelligence.metadata import PROGRESS_TRANSPORT
    from app.schemas.events import StreamEvent

    assert PROGRESS_TRANSPORT == "rest"
    event_schema = json.dumps(TypeAdapter(StreamEvent).json_schema()).lower()
    assert "code_intelligence" not in event_schema
    assert "code-intelligence" not in event_schema
