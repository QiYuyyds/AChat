# Delta: adapters

## ADDED Requirements

### Requirement: CustomAgentAdapter SHALL coalesce incremental part.delta events

The CustomAgentAdapter MUST coalesce `text.append`, `thinking.append`, and `file_write_preview.append` delta events using a time-windowed coalescer (default 50ms window) before yielding them from `stream()` / `call_once()`. The coalescer MUST flush pending deltas on `part.end`, on any non-delta event, and on turn end, as specified in the stream-events delta.

The coalescer MUST be transparent to downstream consumers: the `part.delta` schema, the `consume_stream` event loop, and the `EventBus` publish path MUST NOT be modified. Coalescing happens entirely within the adapter's event generation layer.

The coalescer window size MUST be configurable via a constructor parameter or module-level constant, defaulting to 50ms, to allow tuning without code changes.

#### Scenario: CustomAdapter streams a long markdown summary

- **WHEN** the OpenAI Chat Completions stream yields 200 `content` chunks for a single text part over 5 seconds
- **THEN** the adapter coalesces chunks within each 50ms window
- **AND** emits approximately 100 merged `part.delta` events (instead of 200 individual ones)
- **AND** each merged event's `delta.text` is the concatenation of all chunks in that window
- **AND** `consume_stream` and the frontend receive identical final content as without coalescing.

#### Scenario: CustomAdapter streams a large file write preview

- **WHEN** the `_ContentExtractor` yields 500 `file_write_preview.append` chunks for a single `fs_write` tool call
- **THEN** the adapter coalesces chunks within each 50ms window
- **AND** emits approximately 50-100 merged `part.delta` events (instead of 500 individual ones)
- **AND** the coalescer flushes all pending deltas before emitting the `part.end` for the preview part
- **AND** the coalescer flushes all pending deltas before emitting `tool.call` for the same tool call index.

#### Scenario: Coalescer flushes on part.end

- **WHEN** a `part.end` event is about to be yielded for a part with pending coalesced deltas
- **THEN** the adapter first yields the merged `part.delta` with all buffered text for that part
- **AND** then yields the `part.end` event
- **AND** the coalescer's buffer for that `(part_index, delta.type)` is cleared.

#### Scenario: Coalescer is transparent to consume_stream

- **WHEN** the adapter uses the coalescer
- **THEN** `consume_stream` in `agent_runner.py` requires no changes
- **AND** the `EventBus.publish` path requires no changes
- **AND** the `part.delta` event schema (field names, types) is unchanged
- **AND** only the `text` field's value may be larger (concatenated) than without coalescing.
