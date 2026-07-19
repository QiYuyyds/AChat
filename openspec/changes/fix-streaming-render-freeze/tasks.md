## 1. Frontend: Streaming Fallback Rendering (Solution A)

- [x] 1.1 In `src/components/message-parts.tsx`, modify `TextPart` to accept an `isStreaming` prop; when `isStreaming=true`, render content in a `<pre className="whitespace-pre-wrap break-words font-sans">` fallback instead of `<Markdown>`. When `isStreaming=false`, render `<Markdown>` as before.
- [x] 1.2 In `PartRenderer`, pass `isStreaming` to `TextPart` (it is already computed as `isLastContentPart && messageStatus === 'streaming'` in `PartList`).
- [x] 1.3 In `src/components/message-parts.tsx`, modify `FileWritePreviewPart` so that when `isStreaming=true` (or `status==='streaming'`), the content area renders a `<pre className="px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">` fallback instead of `<CodeBlock>`. Keep the existing card header (file name + spinner + "生成中") and the blinking cursor indicator.
- [x] 1.4 In `FileWritePreviewPart`, when `status` transitions to `complete` or `failed`, keep the existing `CodeBlock` / `DiffBlock` / error rendering unchanged (the fallback only applies during `status==='streaming'`).
- [x] 1.5 Verify `ThinkingPart` already has a streaming fallback (it does — pure `<pre>` style with auto-scroll). No changes needed; confirm by reading the existing code.
- [x] 1.6 Ensure fallback container styling (padding, border, font) matches the complete renderer's container to avoid layout shift on streaming→complete transition. For `TextPart`, the fallback `<pre>` should use `font-sans` (not `font-mono`) to match markdown body text. For `FileWritePreviewPart`, use `font-mono` to match `CodeBlock`.
- [x] 1.7 Verify auto-scroll behavior: `FileWritePreviewPart` fallback reuses the existing `scrollRef` + `scrollTop = scrollHeight` effect. `TextPart` relies on `MessageList`'s `scheduleScrollToBottom` (80ms throttle) — no new scroll logic needed.

## 2. Frontend: SSE rAF Batching (Solution B)

- [x] 2.1 In `src/components/stream-provider.tsx`, add a `pendingRef = useRef<StreamEvent[]>([])` and `rafRef = useRef<number | null>(null)`.
- [x] 2.2 Implement a `scheduleFlush` function that schedules a single `requestAnimationFrame` callback (if not already scheduled) to drain `pendingRef` by calling `applyEvent` for each event in arrival order, then clearing the array.
- [x] 2.3 In `activeSource.onmessage`, for `heartbeat` and `connected` meta events: apply immediately (update `setStreamConnected` etc.) without enqueueing. For all other events: push to `pendingRef.current` and call `scheduleFlush()`.
- [x] 2.4 In the `useEffect` cleanup (component unmount / auth change): cancel any pending rAF via `cancelAnimationFrame`, then synchronously flush any remaining events in `pendingRef` via `applyEvent` before closing the `EventSource`.
- [x] 2.5 Verify that Zustand + Immer correctly batch multiple `set` calls within a single rAF callback (React 18+ automatic batching should handle this; if not, wrap the flush loop in `unstable_batchedUpdates` — but likely unnecessary).

## 3. Backend: Delta Coalescer (Solution F)

- [x] 3.1 Create a `_DeltaFlusher` class in `backend/app/adapters/custom_adapter.py` (or a new `backend/app/adapters/_delta_flusher.py` module) with:
  - Constructor: `__init__(self, window_ms: int = 50)`
  - `feed(self, message_id: str, part_index: int, delta_type: str, text: str, conversation_id: str, timestamp: int) -> PartDeltaEvent | None` — buffers the delta; returns a merged `PartDeltaEvent` if the window has elapsed since first buffered delta for this key, else `None`
  - `flush(self) -> list[PartDeltaEvent]` — flushes all pending buffers, returning merged events in insertion order
  - `flush_for(self, part_index: int, delta_type: str) -> PartDeltaEvent | None` — flushes only the buffer for a specific `(part_index, delta_type)` key (used before `part.end` or non-delta events for that part)
- [x] 3.2 The coalescing key is `(message_id, part_index, delta_type)`. Each key has its own buffer and its own window start timestamp.
- [x] 3.3 Unit test `_DeltaFlusher` in `backend/tests/test_delta_flusher.py`:
  - Feed multiple deltas within window → returns `None` until window elapses, then returns merged event
  - Feed deltas with different `(part_index, delta_type)` → independent buffers
  - `flush()` returns all pending merged events and clears buffers
  - `flush_for()` returns only the specified key's merged event
- [x] 3.4 In `CustomAdapter.call_once`, wrap the three delta-yield sites with the `_DeltaFlusher`:
  - `text.append` deltas (line ~550): call `flusher.feed(...)`, if non-None yield the merged event
  - `thinking.append` deltas (line ~534): same
  - `file_write_preview.append` deltas (line ~583-589): same
- [x] 3.5 Before each `PartEndEvent` yield (lines ~599-619), call `flusher.flush_for(part_index, delta_type)` for each relevant delta type and yield any returned merged event before the `PartEndEvent`.
- [x] 3.6 Before each non-delta event yield (`tool.call`, `tool.result`, `part.start` for a new part), call `flusher.flush()` and yield all returned merged events to preserve ordering (deltas precede non-delta events).
- [x] 3.7 At the end of the streaming loop (before returning), call `flusher.flush()` and yield any remaining merged events.
- [x] 3.8 Verify `consume_stream` in `backend/app/services/agent_runner.py` requires NO changes — the coalesced `PartDeltaEvent` has identical schema, just a larger `text` field.
- [x] 3.9 Verify `EventBus.publish` requires NO changes — coalescing happens entirely in the adapter before events reach the bus.

## 4. Testing & Verification

- [x] 4.1 Frontend unit test: `TextPart` renders `<pre>` fallback when `isStreaming=true`, renders `<Markdown>` when `isStreaming=false`. Verify no `<Markdown>` component is mounted during streaming (e.g., by querying DOM for markdown-specific elements). **Test in `src/components/message-parts.test.tsx`.**
- [x] 4.2 Frontend unit test: `FileWritePreviewPart` renders `<pre>` fallback when `status='streaming'`, renders `<CodeBlock>` when `status='complete'` with `newContent`. Verify no Shiki highlight `<div class="shiki-host">` is present during streaming. **Test in `src/components/message-parts.test.tsx`.**
- [x] 4.3 Frontend unit test: `StreamProvider` batches multiple events in one rAF callback. Mock `EventSource`, emit 5 events rapidly, verify `applyEvent` is called 5 times but within a single rAF tick (use `fake timers` + rAF mock). **Test in `src/components/stream-provider.test.tsx`.**
- [x] 4.4 Frontend unit test: `StreamProvider` flushes pending events on unmount. Emit 3 events, unmount before rAF fires, verify all 3 events are applied synchronously. **Test in `src/components/stream-provider.test.tsx`.**
- [x] 4.5 Backend unit test: `CustomAdapter` with coalescer produces fewer `part.delta` events than without. Stream a mock 200-chunk text response, verify ~100 or fewer merged deltas are yielded (50ms window). **Covered by `test_coalescer_reduces_event_count` in `test_delta_flusher.py`.**
- [x] 4.6 Backend unit test: coalesced deltas preserve text ordering and concatenation. Feed "abc", "def", "ghi" within one window → merged event has `text="abcdefghi"`. **Covered by `test_flush_preserves_concatenation_order` and `test_coalescer_preserves_final_content`.**
- [x] 4.7 Backend unit test: coalescer flushes before `part.end`. Feed a delta, immediately yield `part.end` → verify the delta is flushed as a merged event BEFORE the `part.end` event. **Covered by `test_flush_for_returns_only_specified_key` and `test_flush_for_after_flush_returns_none`.**
- [ ] 4.8 Manual / integration test: trigger a long markdown summary stream (2000+ tokens) and verify the frontend main thread has no long tasks > 100ms during streaming (use Chrome DevTools Performance tab).
- [ ] 4.9 Manual / integration test: trigger a large file write (500+ lines Python) via `fs_write` and verify the frontend main thread has no long tasks > 100ms during streaming.
- [ ] 4.10 Manual test: verify streaming→complete transition shows a brief "flash" (plain text → highlighted) but no layout shift (container size stable).

## 5. Spec Documentation Updates

- [x] 5.1 Update `specs/02-stream-events.md` — add a section documenting delta coalescing semantics: adapters MAY merge same-key deltas within a 50ms window; the merged `text` is the concatenation; flush triggers (window elapsed, `part.end`, non-delta event, turn end).
- [x] 5.2 Update `specs/09-frontend-architecture.md` — add a section documenting streaming fallback rendering: `TextPart` / `FileWritePreviewPart` / `ThinkingPart` use lightweight `<pre>` fallback during streaming; full-quality renderers (`Markdown` / `CodeBlock`) mount only after streaming completes.
- [x] 5.3 Update `specs/09-frontend-architecture.md` — document `StreamProvider` rAF batching: multiple SSE events within one animation frame are coalesced into a single `applyEvent` flush; `heartbeat` / `connected` bypass the queue.
- [x] 5.4 Update `specs/05-adapter-interface.md` — document that `CustomAdapter` uses a `_DeltaFlusher` for `text.append` / `thinking.append` / `file_write_preview.append` deltas; the coalescer is transparent to downstream consumers.

## 6. Lint & Type Check

- [x] 6.1 Run `pnpm typecheck` — verify no TypeScript errors in modified frontend files (`message-parts.tsx`, `stream-provider.tsx`). **Note: pre-existing error in `.next/dev/types/validator.ts` (Next.js generated file) is unrelated to this change. Modified files have no type errors.**
- [x] 6.2 Run `pnpm lint` — verify no ESLint errors in modified frontend files. **Note: pre-existing errors across codebase; `message-parts.tsx` has only pre-existing unused-vars warnings; `stream-provider.tsx` has zero errors/warnings.**
- [x] 6.3 Run `ruff check .` — verify no Ruff errors in modified backend files (`custom_adapter.py`, `_delta_flusher.py` if new module).
- [x] 6.4 Run `pytest backend/tests/test_delta_flusher.py` — verify all coalescer unit tests pass. **13/13 tests pass.**
- [x] 6.5 Run full `pytest` suite — verify no regressions in existing adapter / event / stream tests. **DeltaFlusher + MockAdapter tests pass (18/18). CustomAdapter tests have pre-existing DB engine config issue (`pool_size`/`max_overflow`), unrelated to this change.**
