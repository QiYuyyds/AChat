# Delta Spec: message-parts

## ADDED Requirements

### Requirement: file_write_preview MessagePart Type

The `MessagePart` union type SHALL include a new discriminated variant:

```typescript
| {
    type: 'file_write_preview'
    path: string
    content: string
    callId: string
    status: 'streaming' | 'complete' | 'failed'
    language?: string
    oldContent?: string | null
    newContent?: string | null
  }
```

Fields:
- `path`: The workspace-relative file path being written
- `content`: The streaming or final file content
- `callId`: Matches the corresponding `tool_use` part's `callId`
- `status`: `streaming` during LLM generation, `complete` after tool execution, `failed` on error
- `language`: Optional language identifier for syntax highlighting (derived from file extension)
- `oldContent`: Previous file content (null for new files, set on `complete`)
- `newContent`: Final written content (set on `complete`)

#### Scenario: Part appears in message parts array

- **WHEN** a `file_write_preview` part is created
- **THEN** it SHALL be stored at the next available `partIndex` in the message's `parts` array
- **AND** it SHALL be rendered by the `FileWritePreviewPart` component

### Requirement: file_write_preview.append PartDelta Type

The `PartDelta` union type SHALL include a new variant:

```typescript
| { type: 'file_write_preview.append'; text: string }
```

#### Scenario: Delta applied to file_write_preview part

- **WHEN** a `part.delta` event with `{ type: 'file_write_preview.append', text }` is received
- **THEN** the `text` SHALL be appended to the `content` field of the corresponding `file_write_preview` part
