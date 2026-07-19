## ADDED Requirements

### Requirement: Transcript renderer SHALL include tool_use and tool_result in transcript

The `render_tool_aware_transcript()` function MUST render each agent message as multiple lines: the agent's text content (if any), followed by each `tool_use` part as `↳ tool_use: <tool_name>(<args>)` and each `tool_result` part as `↳ tool_result: [<tool_name>] <summary> | <compressed_content>`. User and system messages MUST be rendered as `<role>：<text>` (single line). Messages with no text and no tool parts MUST be skipped. The function MUST NOT render `thinking` parts (they are private to the agent and not useful for summarization).

#### Scenario: Agent message with tool calls renders multi-line transcript

- **WHEN** an agent message has one `text` part ("让我看看项目结构"), one `tool_use` part (`fs_list(path='src', depth=3)`), and one `tool_result` part (fs_list result with 50 entries)
- **THEN** the transcript renders as:
  ```
  Agent：让我看看项目结构
    ↳ tool_use: fs_list(path='src', depth=3)
    ↳ tool_result: [fs_list] src/ 含 5 文件、3 子目录 | [{"name":"index.ts","relativePath":"src/index.ts"},...]
  ```
- **AND** the tool_result content is compressed via `summarize_tool_result(stage=1)` before rendering

#### Scenario: User message renders as single line

- **WHEN** a user message has one `text` part ("分析一下这个项目")
- **THEN** the transcript renders as `用户：分析一下这个项目`
- **AND** no tool_use or tool_result lines are emitted

### Requirement: Transcript renderer SHALL compress tool_result using Tier 0 strategy table

The `render_tool_aware_transcript()` function MUST call `compact_pipeline.summarize_tool_result(tool_name, args, content, stage=1)` for each `tool_result` part before rendering it. The `stage=1` (light) strategy MUST be used because transcript output is consumed by an LLM summarizer that needs maximum information density. `code_explore` results MUST be preserved verbatim (the strategy table already guarantees this). `fs_read(mode="outline")` and `fs_read(mode="head")` results MUST be preserved verbatim.

#### Scenario: fs_list result is compressed in transcript

- **WHEN** a `tool_result` part contains a `fs_list(depth=3)` result with 500 entries (~15k tokens)
- **THEN** the transcript renders the compressed version (name + relativePath only, ~3k tokens)
- **AND** the compression uses `summarize_tool_result("fs_list", args, content, stage=1)`

#### Scenario: code_explore result is preserved verbatim in transcript

- **WHEN** a `tool_result` part contains a `code_explore` result (~7.5k tokens)
- **THEN** the transcript renders the full content without compression
- **AND** no information is lost

### Requirement: estimate_full_message_tokens SHALL count all message parts

The `estimate_full_message_tokens()` function MUST estimate tokens from ALL parts of a message: `text`, `thinking`, `effective_prompt`, `tool_use` (args), and `tool_result` (result). This replaces the Session Memory's text-only estimation which severely underestimates context size for tool-heavy turns. The function MUST be shared between Session Memory and Tier 2/3 to ensure consistent token accounting.

#### Scenario: Tool-heavy message estimates higher than text-only

- **WHEN** a message has one `text` part (500 tokens) and three `tool_result` parts (50k tokens total)
- **THEN** `estimate_full_message_tokens` returns ~50.5k tokens
- **AND** the legacy `estimate_tokens(_message_text(msg))` would have returned only ~500 tokens

#### Scenario: Session Memory should_extract uses full token estimate

- **WHEN** `SessionMemory.should_extract` evaluates trigger conditions on a conversation with 5 tool-heavy turns
- **THEN** the token estimate includes tool_use and tool_result parts
- **AND** the estimate is significantly higher than the legacy text-only estimate

## MODIFIED Requirements

### Requirement: Session Memory summary prompt SHALL preserve structured dimensions

The `SessionMemory.extract` system prompt MUST instruct the LLM to preserve the following dimensions in the summary, in addition to the existing "user goals, key decisions, artifacts, pending items":

- Explored file/directory structures (paths + key findings)
- Key commands executed and their result summaries
- Architecture understanding and code structure discoveries

#### Scenario: Summary includes file structure from tool results

- **WHEN** Session Memory extracts a summary for a conversation where the agent used `fs_list(depth=3)` and `fs_read` to explore the project
- **THEN** the resulting summary mentions the explored directory paths and key file findings
- **AND** the summary is not limited to the agent's text responses

### Requirement: Tier 2/3 compaction summary prompt SHALL preserve structured dimensions

The `compact_conversation` → `_summarise` prompt MUST instruct the LLM to preserve the same structured dimensions as Session Memory (file structures, command results, architecture findings), in addition to the existing "user goals, key decisions, artifacts, pending items".

#### Scenario: Compaction summary includes architecture findings

- **WHEN** `compact_conversation` summarizes a conversation where the agent explored code and reported architecture findings
- **THEN** the resulting `ContextSummary.summary` includes the architecture findings
- **AND** the next `build_history_for` call injects this summary, giving the agent cross-run memory of the exploration
