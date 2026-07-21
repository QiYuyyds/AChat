# Frontend Delta: Chat UI Refinement

## ADDED Requirements

### Requirement: Message parts SHALL render as alternating process and conclusion segments

`PartList` MUST classify each `MessagePart` as either a process-type part (`thinking`, `tool_use`, `file_write_preview`) or a conclusion-type part (`text`, `artifact_ref`, `deploy_status`, `execution_plan`, `deploy_candidates`, `image_attachment`, `file_attachment`). Consecutive process-type parts MUST be grouped into a single `ProcessSegment` rendered in-place. Conclusion-type parts MUST render individually. The original part ordering MUST be preserved — the rendered sequence is an alternating series of process segments and conclusion parts.

#### Scenario: Agent message with mixed parts
- **WHEN** an agent message contains `[thinking, tool_use, text, tool_use, text]`
- **THEN** PartList renders `[ProcessSegment(thinking+tool_use), TextBubble, ProcessSegment(tool_use), TextBubble]`
- **AND** the original narrative order is preserved.

#### Scenario: Consecutive tool_use parts without intervening text
- **WHEN** an agent message contains `[tool_use, tool_use, tool_use, text]`
- **THEN** PartList renders a single `ProcessSegment` containing all three tool_use parts followed by a `TextBubble`
- **AND** the previous `ToolCluster` logic is replaced by `ProcessSegment`.

### Requirement: Process segments SHALL auto-collapse when the message reaches a terminal status

A `ProcessSegment` MUST be expanded (showing full process details) while `message.status === 'streaming'`. When `message.status` transitions to any terminal value (`complete`, `error`, `aborted`, `interrupted`), the `ProcessSegment` MUST automatically collapse to a one-line summary. The user MAY click the summary to manually expand and inspect process details.

The collapsed summary format MUST follow:
- Contains thinking: `▸ 思考 <duration> · <N> 个工具 · <total_duration>`
- No thinking, has tools: `▸ <N> 个工具 · <total_duration>`
- Only thinking: `▸ 已深度思考 <duration>`

#### Scenario: Streaming message shows process details
- **WHEN** an agent message has `status='streaming'`
- **THEN** all `ProcessSegment` instances are expanded
- **AND** thinking, tool_use, and file_write_preview parts are visible as borderless compact rows.

#### Scenario: Message completes auto-collapses process segments
- **WHEN** an agent message transitions from `status='streaming'` to `status='complete'`
- **THEN** all `ProcessSegment` instances collapse to one-line summaries
- **AND** only text bubbles and other conclusion parts remain as visual focal points.

#### Scenario: User manually expands a collapsed process segment
- **WHEN** the user clicks a collapsed process segment summary
- **THEN** the segment expands to show full process details
- **AND** clicking again collapses it back.

### Requirement: Text parts SHALL render with individual bubbles

Each `text` part MUST render inside its own bubble with `bg-card` background, `rounded-lg` border, and `px-4 py-3` padding. The outer message-level bubble (`bg-card` on `MessageItem`'s content div) MUST be removed. Process segments MUST NOT have bubbles, borders, or background colors — they render as borderless compact text to create visual contrast with text bubbles.

For user messages, the text bubble MUST use `bg-primary/5` with `border-l-2 border-primary`. For agent messages, the text bubble MUST use `bg-card` with `border border-border/50`.

#### Scenario: Agent text part renders as bubble
- **WHEN** an agent message has a `text` part with content
- **THEN** the text renders inside a `bg-card` bubble with `px-4 py-3` padding and a subtle border
- **AND** no outer message-level bubble wraps the entire message.

#### Scenario: User text part renders as bubble
- **WHEN** a user message has a `text` part
- **THEN** the text renders inside a `bg-primary/5` bubble with `border-l-2 border-primary`.

#### Scenario: Process segment renders without bubble
- **WHEN** a ProcessSegment is expanded
- **THEN** thinking, tool_use, and file_write_preview parts render as borderless compact rows
- **AND** no `bg-card`, `border`, or `shadow` is applied to the process segment container.

### Requirement: Process segment internal rendering SHALL use borderless compact style

When a `ProcessSegment` is expanded, its internal parts MUST NOT render as `Card` components (no `border`, no `bg-*` background from Card). Instead:
- `thinking` parts render as `text-xs text-muted-foreground/70 italic` plain text
- `tool_use` parts render as a single-line summary (`icon + name + status + duration`) in `text-xs text-muted-foreground`; consecutive tool_use parts stack vertically with `space-y-0.5`
- `file_write_preview` parts render as a compact file-name + status line; diff/code preview areas retain their existing styling
- Tool detail expansion (parameters, return values) uses inline `bg-muted/40 rounded` blocks, not `Card`

#### Scenario: Expanded process segment shows borderless tool calls
- **WHEN** a ProcessSegment is expanded and contains two tool_use parts
- **THEN** each tool_use renders as a single-line summary without Card border or background
- **AND** the two summaries stack vertically with minimal spacing.

### Requirement: Font SHALL use Manrope for sans-serif text

The application MUST load Manrope via `next/font/google` with `variable: '--font-manrope'` and `subsets: ['latin']`. The `--font-sans` CSS variable MUST reference `--font-manrope`. Geist Mono MUST remain as the `--font-mono` for code blocks and terminal output. Message meta information rows (name, time, token count) MUST use `font-sans tabular-nums` instead of `font-mono`.

#### Scenario: Page loads with Manrope font
- **WHEN** the application loads
- **THEN** sans-serif text renders in Manrope
- **AND** code blocks and terminal output still render in Geist Mono.

#### Scenario: Message meta row uses sans-serif
- **WHEN** a message renders its meta row (name + time + token count)
- **THEN** the text uses `font-sans tabular-nums` (not `font-mono`)
- **AND** numeric values remain column-aligned via `tabular-nums`.

## MODIFIED Requirements

### Requirement: Store reducers SHALL apply StreamEvent deterministically

Zustand reducers MUST update conversation, message, artifact, pending write, pending bash command, dispatch, and usage state from `StreamEvent` payloads. This requirement is unchanged — the store layer is not affected by the chat UI refinement. Message part data structures (`MessagePart` type) are not modified; only the rendering layer (`PartList`, `MessageItem`, `TextPart`, etc.) changes.

#### Scenario: `part.delta` arrives
- **WHEN** the event references an existing part
- **THEN** the store appends content to that part without reordering other parts.

#### Scenario: A failed run leaves an open tool call
- **WHEN** `run.end` arrives with `status='failed'` or `status='aborted'`
- **THEN** the store marks streaming messages from that run as terminal
- **AND** appends local error `tool_result` parts for any unmatched `tool_use` call ids.
