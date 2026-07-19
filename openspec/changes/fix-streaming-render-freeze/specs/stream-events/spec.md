# Delta: stream-events

## ADDED Requirements

### Requirement: Adapters MAY coalesce incremental part.delta events

Adapters MAY merge multiple `part.delta` events with the same `(message_id, part_index, delta.type)` key within a bounded time window (default 50ms) into a single `part.delta` event whose `text` field is the concatenation of all merged deltas' `text` fields. This coalescing is a producer-side optimization that reduces SSE event volume without changing `part.delta` semantics: the concatenated `text` is still an append to the same part.

Coalescing MUST be bounded: the adapter MUST flush all pending coalesced deltas when any of the following occurs:
- The time window elapses (default 50ms)
- A `part.end` event for the same part is about to be emitted
- A non-delta event (e.g., `tool.call`, `tool.result`, `part.start`) is about to be emitted for the same message
- The streaming turn ends (`message.end`)

After flushing, the adapter MUST emit the merged `part.delta` before the triggering non-delta event to preserve event ordering: all deltas for a part precede its `part.end`.

Coalescing MUST NOT be applied to:
- `part.start`, `part.end`, `tool.call`, `tool.result`, `message.start`, `message.end`, or any non-delta event
- Deltas with different `delta.type` (e.g., `text.append` and `thinking.append` are coalesced independently)
- Deltas with different `part_index` (each part has its own coalescing buffer)

#### Scenario: Multiple text.append deltas within one window

- **WHEN** an adapter receives 5 `text.append` deltas for the same `(message_id, part_index)` within 50ms
- **THEN** the adapter emits a single `part.delta` event with `delta.type='text.append'` and `delta.text` equal to the concatenation of all 5 deltas' text
- **AND** no individual per-chunk `part.delta` events are emitted for those 5 chunks.

#### Scenario: Window elapses mid-stream

- **WHEN** an adapter is coalescing `text.append` deltas and 50ms elapses since the first buffered delta
- **THEN** the adapter emits the merged `part.delta` with all buffered text
- **AND** resets the buffer for the next window
- **AND** continues coalescing subsequent deltas in a new window.

#### Scenario: Non-delta event interrupts coalescing

- **WHEN** a `tool.call` event is about to be emitted while `text.append` deltas are buffered
- **THEN** the adapter first flushes all buffered `text.append` deltas as a single merged `part.delta`
- **AND** then emits the `tool.call` event
- **AND** event ordering preserves: all deltas precede the tool call.

#### Scenario: Different delta types are coalesced independently

- **WHEN** an adapter receives interleaved `text.append` and `thinking.append` deltas for different `part_index` values
- **THEN** each `(part_index, delta.type)` pair has its own coalescing buffer
- **AND** a `text.append` delta does not reset or merge with a `thinking.append` buffer
- **AND** each buffer flushes independently when its own window elapses.

#### Scenario: Legacy client receives coalesced delta

- **WHEN** a client that does not implement rAF batching receives a coalesced `part.delta` with a larger `text` field
- **THEN** the client's reducer appends the concatenated text in one operation
- **AND** the final part content is identical to what it would be if deltas were emitted individually
- **AND** no client-side change is required to handle coalescing.
