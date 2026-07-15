# Delta Spec: stream-events

## ADDED Requirements

### Requirement: FileWritePreviewCompleteEvent

A new event type SHALL be added to the `StreamEvent` union:

```typescript
| {
    type: 'file_write_preview.complete'
    conversationId: string
    timestamp: number
    messageId: string
    callId: string
    path: string
    oldContent: string | null
    newContent: string | null
    status: 'complete' | 'failed'
  }
```

This event is emitted after `fs_write` / `fs_edit` tool execution completes, carrying diff data to update the corresponding `file_write_preview` part.

#### Scenario: Successful fs_write completion event

- **WHEN** `_execute_tool_call_to_result` executes `fs_write` successfully
- **THEN** it SHALL append a `FileWritePreviewCompleteEvent` to the tool execution result events
- **AND** `consume_stream` SHALL update the matching `file_write_preview` part in `parts_buffer` with the event's `oldContent`, `newContent`, `path`, and `status`

#### Scenario: consume_stream processes file_write_preview.complete

- **WHEN** `consume_stream` receives a `file_write_preview.complete` event
- **THEN** it SHALL find the `file_write_preview` part in `parts_buffer` that matches `callId`
- **AND** it SHALL update the part's `status`, `oldContent`, `newContent`, `path` fields
- **AND** it SHALL publish the event via SSE (unless hidden run)
- **AND** it SHALL persist the updated `parts_buffer`

### Requirement: Event Visibility

The `file_write_preview.complete` event SHALL be included in `_VISIBLE_EVENT_TYPES` so it is published to SSE for non-hidden runs.

#### Scenario: Hidden run suppression

- **WHEN** a run is hidden (clone-subagent)
- **THEN** `file_write_preview.complete` events SHALL be persisted but NOT published to SSE
