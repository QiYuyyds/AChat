# Delta Spec: adapters

## MODIFIED Requirements

### Requirement: CustomAdapter fs_write Streaming Preview

The CustomAdapter's `call_once` method SHALL detect `fs_write` tool calls during the streaming accumulation phase and produce `file_write_preview` part events with incremental content deltas.

#### Scenario: Detecting fs_write tool call

- **WHEN** the CustomAdapter processes a tool_calls delta (`tcd`) with `tcd.function.name == "fs_write"`
- **THEN** the adapter SHALL initialize a content extraction state machine for that tool call index
- **AND** yield a `PartStartEvent` with part type `file_write_preview`, `callId` from `tcd.id`, `status: 'streaming'`, and `path` extracted from the args_buffer if available

#### Scenario: Streaming content from args_buffer increments

- **WHEN** the content extraction state machine is in `IN_STRING` state and receives a new args_buffer increment
- **THEN** the adapter SHALL decode the JSON string escape sequences in the increment
- **AND** yield a `PartDeltaEvent` with delta type `file_write_preview.append` and the decoded text

#### Scenario: Content extraction state machine transitions

- **WHEN** the args_buffer accumulation transitions through the `content` field value
- **THEN** the state machine SHALL progress through states: `IDLE` → `FOUND_KEY` → `WAIT_VALUE` → `IN_STRING` → `DONE`
- **AND** only content emitted during `IN_STRING` state SHALL be sent as delta events

#### Scenario: Path extraction from args_buffer

- **WHEN** the state machine detects the `path` field value in the args_buffer
- **THEN** the adapter SHALL store the extracted path value
- **AND** if the path was not available at `part.start` time, the adapter SHALL NOT retroactively update the part (the path will be filled by `FileWritePreviewCompleteEvent`)

#### Scenario: Multiple tool calls in a single turn

- **WHEN** the LLM emits multiple `fs_write` tool calls in a single turn
- **THEN** each tool call SHALL have its own independent content extraction state machine
- **AND** each SHALL produce its own `file_write_preview` part with a unique `callId`

#### Scenario: Non-fs_write tool calls

- **WHEN** the LLM emits a tool call with name other than `fs_write`
- **THEN** no `file_write_preview` part SHALL be produced by the adapter
- **AND** the standard `tool.call` event flow SHALL proceed unchanged

### Requirement: stream method not affected

The CustomAdapter's `stream` method (legacy self-loop) SHALL NOT produce `file_write_preview` parts.

#### Scenario: Legacy stream method

- **WHEN** the `stream` method is used
- **THEN** no `file_write_preview` parts or deltas SHALL be yielded
- **AND** tool execution results SHALL still include `oldContent` / `newContent` in the result value (handled by the tool handler, not the adapter)
