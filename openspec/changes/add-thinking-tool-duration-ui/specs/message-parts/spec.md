## MODIFIED Requirements

### Requirement: Thinking content SHALL be distinct from text content

Reasoning or planning output SHALL use `thinking` parts rather than regular `text` parts when the adapter can identify it. The `thinking` part type SHALL include optional `startedAt` and `endedAt` fields (Unix epoch milliseconds) to enable duration display.

- `thinking` part type: `{ type: 'thinking'; content: string; startedAt?: number; endedAt?: number }`
- `startedAt` SHALL be captured from the `part.start` event's `timestamp` field by the store reducer
- `endedAt` SHALL be captured from the `part.end` event's `timestamp` field by the store reducer
- Both fields are optional for backward compatibility; parts without them SHALL render without duration display

#### Scenario: DeepSeek returns reasoning content

- **WHEN** a streamed delta includes `reasoning_content`
- **THEN** CustomAgentAdapter appends it to a `thinking` part
- **AND** preserves it for follow-up DeepSeek tool turns.

#### Scenario: Thinking part receives timing data

- **WHEN** a `part.start` event arrives for a `thinking` part at index `i`
- **THEN** the store SHALL set `msg.parts[i].startedAt` to the event's `timestamp`
- **AND** when a `part.end` event arrives for the same index, the store SHALL set `msg.parts[i].endedAt` to that event's `timestamp`

#### Scenario: Historical message loaded without timing fields

- **WHEN** a message is loaded from the database where `thinking` parts lack `startedAt` / `endedAt`
- **THEN** the UI SHALL render the thinking part without duration display
- **AND** no error or crash occurs

### Requirement: Tool parts SHALL carry timing data

The `tool_use` and `tool_result` part types SHALL include optional timing fields to enable per-tool duration display.

- `tool_use` part type: `{ type: 'tool_use'; callId: string; toolName: string; args: unknown; startedAt?: number }`
- `tool_result` part type: `{ type: 'tool_result'; callId: string; result: unknown; isError: boolean; endedAt?: number }`
- `tool_use.startedAt` SHALL be captured from the `tool.call` event's `timestamp` by the store reducer
- `tool_result.endedAt` SHALL be captured from the `tool.result` event's `timestamp` by the store reducer
- Both fields are optional for backward compatibility

#### Scenario: Tool call receives start timestamp

- **WHEN** a `tool.call` event arrives with `timestamp=T` for `callId=C`
- **THEN** the store SHALL set the `tool_use` part with `callId=C` to have `startedAt=T`
- **AND** persist this field into the database parts JSON

#### Scenario: Tool result receives end timestamp

- **WHEN** a `tool.result` event arrives with `timestamp=T` for `callId=C`
- **THEN** the store SHALL set the `tool_result` part with `callId=C` to have `endedAt=T`
- **AND** the UI SHALL display `duration = endedAt - startedAt` next to the tool call

#### Scenario: Tool parts without timing data

- **WHEN** historical tool_use / tool_result parts lack `startedAt` / `endedAt`
- **THEN** the UI SHALL render the tool call without duration display
- **AND** no error or crash occurs
