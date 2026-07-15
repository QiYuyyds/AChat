# Delta Spec: frontend

## ADDED Requirements

### Requirement: FileWritePreviewPart Component

A new `FileWritePreviewPart` React component SHALL render `file_write_preview` parts with three visual modes.

#### Scenario: Streaming mode rendering

- **WHEN** a `file_write_preview` part has `status: 'streaming'`
- **THEN** the component SHALL render a code block with the `content` text
- **AND** the code block SHALL have a green-tinted background to indicate "content being generated"
- **AND** a blinking cursor SHALL appear at the end of the content
- **AND** the header SHALL display the file path (or "正在写入..." if path is empty) and a streaming indicator
- **AND** the code SHALL be syntax-highlighted based on the `language` field (or derived from file extension)

#### Scenario: Complete mode with diff

- **WHEN** a `file_write_preview` part has `status: 'complete'` AND `oldContent` is not null
- **THEN** the component SHALL render a unified diff view comparing `oldContent` to `newContent`
- **AND** the diff SHALL use red highlighting for removed lines and green highlighting for added lines
- **AND** the header SHALL display the file path and a "已完成" status indicator

#### Scenario: Complete mode without diff (new file)

- **WHEN** a `file_write_preview` part has `status: 'complete'` AND `oldContent` is null
- **THEN** the component SHALL render the final `newContent` as a syntax-highlighted code block
- **AND** the header SHALL display the file path and a "已创建" status indicator

#### Scenario: Failed mode

- **WHEN** a `file_write_preview` part has `status: 'failed'`
- **THEN** the component SHALL render a compact error indicator with the file path
- **AND** the content area SHALL show the partially streamed content (if any) with a failure overlay

### Requirement: ToolUsePart Inline Diff Rendering

The `ToolUsePart` component SHALL render an inline diff preview when the tool result contains `oldContent` / `newContent` data.

#### Scenario: fs_write/fs_edit with diff data

- **WHEN** a `ToolUsePart` renders a `fs_write` or `fs_edit` tool call AND the `tool.result` contains `oldContent` and `newContent` fields
- **THEN** the component SHALL render a compact unified diff preview below the tool status line
- **AND** the diff preview SHALL display at most 8 changed lines (collapsed by default, expandable)
- **AND** removed lines SHALL have red background, added lines SHALL have green background
- **AND** the diff SHALL include 1 line of context before and after each change block

#### Scenario: fs_write/fs_edit without diff data (legacy results)

- **WHEN** a `ToolUsePart` renders a `fs_write` or `fs_edit` tool call AND the `tool.result` does NOT contain `oldContent` / `newContent`
- **THEN** the component SHALL render as before (status line + expandable JSON details)
- **AND** no diff preview SHALL be shown

#### Scenario: Non-file-write tools

- **WHEN** a `ToolUsePart` renders a tool other than `fs_write` or `fs_edit`
- **THEN** no diff preview SHALL be shown regardless of result content

## MODIFIED Requirements

### Requirement: PartRenderer Dispatch

The `PartRenderer` switch statement SHALL include a case for `file_write_preview` part type.

#### Scenario: PartRenderer encounters file_write_preview part

- **WHEN** `PartRenderer` receives a part with `type: 'file_write_preview'`
- **THEN** it SHALL render the `FileWritePreviewPart` component with the part's fields as props

### Requirement: Store Reducer for file_write_preview Events

The Zustand store reducer SHALL handle `file_write_preview.complete` events and `file_write_preview.append` deltas.

#### Scenario: Reducer processes part.delta with file_write_preview.append

- **WHEN** the reducer receives a `part.delta` event with `delta.type === 'file_write_preview.append'`
- **THEN** it SHALL append `delta.text` to the `content` field of the matching `file_write_preview` part

#### Scenario: Reducer processes file_write_preview.complete

- **WHEN** the reducer receives a `file_write_preview.complete` event
- **THEN** it SHALL find the `file_write_preview` part in the message's parts array that matches `callId`
- **AND** update its `status`, `path`, `oldContent`, `newContent` fields
