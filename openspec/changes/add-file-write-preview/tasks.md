## 1. Backend: Event Schema & Type Extensions

- [x] 1.1 Add `FileWritePreviewCompleteEvent` Pydantic model to `backend/app/schemas/events.py` with fields: `type='file_write_preview.complete'`, `message_id`, `call_id`, `path`, `old_content`, `new_content`, `status`
- [x] 1.2 Add `'file_write_preview.complete'` to `_VISIBLE_EVENT_TYPES` in `backend/app/services/agent_runner.py`
- [x] 1.3 Verify event serialization with a quick unit test (camelCase aliasing, round-trip)

## 2. Backend: Tool Result Diff Data (Direction B)

- [x] 2.1 Modify `fs_write` handler (`backend/app/tools/fs_write.py`): in auto mode, read the old file content before writing, return `oldContent` and `newContent` in the result dict
- [x] 2.2 Modify `fs_write` handler: in review mode, return `oldContent` and `newContent` in the result dict (already available from pending write)
- [x] 2.3 Modify `fs_edit` handler (`backend/app/tools/fs_edit.py`): in auto mode, return `oldContent` (the pre-edit file content) and `newContent` (the post-edit content) in the result dict
- [x] 2.4 Modify `fs_edit` handler: in review mode, return `oldContent` and `newContent` in the result dict
- [ ] 2.5 Verify with manual test: call fs_write/fs_edit via API, confirm result includes diff data

## 3. Backend: FileWritePreviewCompleteEvent in AgentRunner

- [x] 3.1 In `_execute_tool_call_to_result` (`backend/app/services/agent_runner.py`), after `fs_write` execution succeeds, append `FileWritePreviewCompleteEvent` with `path`, `oldContent`, `newContent`, `status='complete'` from the tool result value
- [x] 3.2 In `_execute_tool_call_to_result`, after `fs_edit` execution succeeds, append `FileWritePreviewCompleteEvent` similarly
- [x] 3.3 In `_execute_tool_call_to_result`, when `fs_write`/`fs_edit` fails, append `FileWritePreviewCompleteEvent` with `status='failed'`, `oldContent=null`, `newContent=null`
- [x] 3.4 In `consume_stream`, add handler for `file_write_preview.complete` event: find matching `file_write_preview` part in `parts_buffer` by `callId`, update its `status`/`path`/`oldContent`/`newContent`, persist updated parts_buffer, publish SSE event

## 4. Backend: CustomAdapter Content Extraction State Machine (Direction A)

- [x] 4.1 Implement `_ContentExtractor` state machine class in `backend/app/adapters/custom_adapter.py` with states: `IDLE`, `FOUND_KEY`, `WAIT_VALUE`, `IN_STRING`, `ESCAPE`, `DONE`; methods: `feed(chunk: str) -> list[str]` (returns decoded content chunks), `get_path() -> str | None`
- [x] 4.2 Handle JSON string escape sequences in the state machine: `\"`, `\\`, `\n`, `\t`, `\r`, `\/`, `\uXXXX` (Unicode 4-digit hex)
- [x] 4.3 Unit test the state machine: feed various partial args_buffer sequences and verify extracted content chunks match expected decoded output
- [x] 4.4 In `CustomAdapter.call_once`, when `tcd.function.name == "fs_write"`: create a `_ContentExtractor`, yield `PartStartEvent` with `file_write_preview` part
- [x] 4.5 In `CustomAdapter.call_once`, on subsequent args_buffer increments for the tracked tool_call index: feed increment to the extractor, yield `PartDeltaEvent` for each extracted content chunk
- [x] 4.6 Derive `language` from `path` file extension (e.g., `.tsx` → `typescript`, `.py` → `python`) and include in the `part.start` part if path is available

## 5. Frontend: Type Definitions

- [x] 5.1 Add `file_write_preview` variant to `MessagePart` union type in `src/shared/types.ts` with fields: `type`, `path`, `content`, `callId`, `status`, `language?`, `oldContent?`, `newContent?`
- [x] 5.2 Add `file_write_preview.append` variant to `PartDelta` union type
- [x] 5.3 Add `file_write_preview.complete` variant to `StreamEvent` union type

## 6. Frontend: FileWritePreviewPart Component

- [x] 6.1 Create `FileWritePreviewPart` component in `src/components/message-parts.tsx` with three render modes:
  - **streaming**: Code block with green-tinted background, blinking cursor at end of content, syntax highlighting
  - **complete with diff**: Unified diff view (red removed / green added) using `react-diff-viewer-continued` or a lightweight inline diff renderer
  - **complete new file**: Final syntax-highlighted code block
  - **failed**: Error indicator with partial content
- [x] 6.2 Add `case 'file_write_preview'` to `PartRenderer` switch, rendering `FileWritePreviewPart`
- [x] 6.3 Add `'file_write_preview'` to `lastContentPartIndex` check in `PartList` (so streaming cursor works correctly)
- [x] 6.4 Implement auto-scroll to bottom during streaming (reuse pattern from `ThinkingPart`)

## 7. Frontend: ToolUsePart Inline Diff (Direction B)

- [x] 7.1 In `ToolUsePart`, detect when `toolName` is `fs_write` or `fs_edit` AND `completion.result` contains `oldContent` / `newContent`
- [x] 7.2 Render inline unified diff preview (max 8 changed lines, collapsible) below the tool status line
- [x] 7.3 Style: red background for removed lines, green for added lines, 1 context line per change block

## 8. Frontend: Store Reducer Updates

- [x] 8.1 Add `case 'file_write_preview.complete'` to app-store reducer: find `file_write_preview` part by `callId`, update `status`/`path`/`oldContent`/`newContent`
- [x] 8.2 Add `case` for `delta.type === 'file_write_preview.append'` in `part.delta` reducer: append text to matching `file_write_preview` part's `content`

## 9. Spec Documentation Updates

- [x] 9.1 Update `specs/03-message-parts.md`: add `file_write_preview` part type and `file_write_preview.append` delta type documentation
- [x] 9.2 Update `specs/02-stream-events.md`: add `file_write_preview.complete` event documentation and event flow example
- [x] 9.3 Update `specs/07-tools.md`: document new `oldContent` / `newContent` / `path` fields in `fs_write` and `fs_edit` tool results

## 10. Integration Testing

- [ ] 10.1 End-to-end test: SDK Agent calls `fs_write` to create a new file → verify streaming preview appears in message, transitions to "created" state
- [ ] 10.2 End-to-end test: SDK Agent calls `fs_write` to modify an existing file → verify streaming preview appears, transitions to diff view
- [ ] 10.3 End-to-end test: SDK Agent calls `fs_edit` → verify ToolUsePart shows inline diff
- [ ] 10.4 Test graceful degradation: model outputs malformed args_buffer → verify preview stays empty, tool.result shows diff normally
