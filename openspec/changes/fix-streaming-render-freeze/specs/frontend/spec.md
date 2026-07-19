# Delta: frontend

## ADDED Requirements

### Requirement: Streaming parts SHALL use lightweight fallback rendering

During active streaming (`message.status === 'streaming'` and the part is the last content-bearing part), `TextPart`, `FileWritePreviewPart`, and `ThinkingPart` MUST render using a lightweight fallback (plain `<pre>` with auto-scroll) instead of invoking expensive renderers (`Markdown` / `CodeBlock` with Shiki). When streaming completes (`message.status` transitions to `complete` / `error` / `aborted`, or `part.end` arrives), the part MUST switch back to the full-quality renderer in a single mount transition.

The fallback container styling (padding, border, font family for code) MUST match the complete renderer's container so that the streaming→complete switch does not cause layout shift.

#### Scenario: Text part streams a long markdown summary

- **WHEN** an agent streams a 2000-token markdown summary via `text.append` deltas
- **THEN** during streaming, `TextPart` renders the accumulating content as plain text in a `<pre>` element
- **AND** `Markdown` (react-markdown + remark-gfm) is NOT mounted during streaming
- **AND** each delta appends to the existing `<pre>` text content without re-parsing the full string
- **AND** when `message.status` transitions to `complete`, `TextPart` mounts `Markdown` and unmounts the fallback `<pre>`.

#### Scenario: File write preview streams a large file

- **WHEN** an agent streams a 500-line file via `file_write_preview.append` deltas
- **THEN** during streaming, `FileWritePreviewPart` renders the accumulating content in a `<pre>` with the streaming cursor indicator
- **AND** `CodeBlock` (Shiki) is NOT mounted during streaming
- **AND** each delta appends to the `<pre>` without re-running Shiki on the full content
- **AND** when `file_write_preview.complete` arrives (or `message.status` becomes terminal), `FileWritePreviewPart` mounts `CodeBlock` or `DiffBlock` and unmounts the fallback.

#### Scenario: Streaming→complete switch causes no layout shift

- **WHEN** a streaming part transitions to complete
- **THEN** the container element retains the same padding, border, and font metrics
- **AND** only the inner content region changes (plain text → highlighted/rendered)
- **AND** the viewport scroll position is preserved without jumping.

### Requirement: StreamProvider SHALL batch SSE events per animation frame

The `StreamProvider` MUST coalesce multiple SSE `onmessage` events arriving within the same browser animation frame into a single flush, using `requestAnimationFrame` to schedule the flush. All events queued during a frame MUST be applied via `applyEvent` in arrival order within that single rAF callback.

`heartbeat` and `connected` meta events MUST be applied immediately without going through the rAF queue, since they do not affect rendering and must reflect connection state without delay.

On component unmount, the `StreamProvider` MUST cancel any pending rAF callback and flush any remaining queued events synchronously to avoid event loss.

#### Scenario: Multiple deltas arrive in a single frame

- **WHEN** the SSE connection delivers 10 `part.delta` events within 16ms (one frame)
- **THEN** all 10 events are queued in a pending array
- **AND** a single rAF callback is scheduled (not 10)
- **AND** in the next rAF callback, all 10 events are applied via `applyEvent` in arrival order
- **AND** React performs a single render pass for the batched updates.

#### Scenario: Heartbeat is applied immediately

- **WHEN** a `heartbeat` event arrives during streaming
- **THEN** the `StreamProvider` applies it immediately (or updates connection state) without enqueueing it in the rAF pending array
- **AND** no rAF callback is scheduled solely for the heartbeat.

#### Scenario: Component unmounts with pending events

- **WHEN** the `StreamProvider` unmounts while events are pending in the rAF queue
- **THEN** the pending rAF callback is cancelled
- **AND** all pending events are applied synchronously via `applyEvent` before the cleanup completes
- **AND** no events are lost.
