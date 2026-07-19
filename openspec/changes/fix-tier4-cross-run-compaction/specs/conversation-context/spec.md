## MODIFIED Requirements

### Requirement: prune_old_tool_results SHALL use turn boundaries and per-tool strategies

`prune_old_tool_results` MUST use a turn-boundary finder to identify the most recent `KEEP_RECENT_TURNS = 2` complete turns (imported from `compact_pipeline`). Tool results in messages older than this boundary MUST be pruned using `compact_pipeline.summarize_tool_result(tool_name, args, content, stage=1)` (the Tier 0 light strategy). The pruned result MUST be replaced with a structured marker built by `CompactMarkerBuilder.build_tool_result_marker`, carrying `stage`, `tool`, `summary`, and `recover` fields. The legacy uniform `TOOL_RESULT_PRUNE_THRESHOLD` (2000 tokens) and `TOOL_RESULT_RECENT_TURNS` (3) constants MUST be removed.

#### Scenario: Tool result pruned with structured marker and recover hint

- **WHEN** `prune_old_tool_results` processes a `tool_result` part from a `fs_list(path='src', depth=3)` call that is 3 turns old
- **THEN** the part is replaced with a `type=text` marker built by `CompactMarkerBuilder.build_tool_result_marker`
- **AND** the marker includes `stage=1`, `tool=fs_list`, a `summary` field describing the content, and a `recover` field suggesting `fs_list(path='src', depth=3) 重新获取结构`
- **AND** the marker does NOT exceed 500 characters

#### Scenario: code_explore result is never pruned in cross-run history

- **WHEN** `prune_old_tool_results` encounters a `code_explore` `tool_result` part in an old turn
- **THEN** the part is preserved verbatim (no marker substitution)
- **AND** this matches the Tier 0 behavior where `code_explore` is always preserved

#### Scenario: Turn boundary protects recent tool results

- **WHEN** a conversation has 4 complete turns and `prune_old_tool_results` runs with `KEEP_RECENT_TURNS=2`
- **THEN** tool results in the most recent 2 turns are preserved verbatim
- **AND** only tool results in turns 1-2 are pruned
- **AND** no `tool_use ↔ tool_result` pair is split across the boundary

### Requirement: fold_old_messages SHALL use turn boundaries and structured fold markers

`fold_old_messages` MUST use a turn-boundary finder to identify complete turns. When the number of complete turns exceeds `FOLD_TURN_THRESHOLD = 4` (imported from `compact_pipeline`), older turns (beyond the most recent `KEEP_RECENT_TURNS = 2`) MUST be replaced with a single fold marker built by `CompactMarkerBuilder.build_fold_marker`. The fold marker MUST include `tools_used` (top 5 tools with counts), `summary`, `first_user` head, and `last_reply` head. Pinned messages MUST be preserved regardless of fold boundary. The legacy `FOLD_THRESHOLD` (30) and `FOLD_KEEP_RECENT` (20) constants MUST be removed. If no complete turn boundary is found, the function MUST fall back to `LEGACY_RECENT_KEEP = 6` (count-based) and log a warning.

#### Scenario: Fold marker includes tools used and summary

- **WHEN** `fold_old_messages` folds 3 older turns that used `fs_list` × 2, `fs_read` × 5, and `bash` × 1
- **THEN** the fold marker includes `tools_used: fs_list×2 fs_read×5 bash×1` (top 5)
- **AND** the marker includes a `summary` field of at most 200 characters
- **AND** the marker includes `first_user` and `last_reply` heads (≤ 80 chars each)
- **AND** the marker total length does not exceed 500 characters

#### Scenario: Pinned messages survive fold

- **WHEN** `fold_old_messages` folds older turns and a pinned message exists in the old segment
- **THEN** the pinned message is preserved in the output (not folded into the marker)
- **AND** the pinned message appears in its original chronological position relative to the fold marker

#### Scenario: Fallback when no turn boundary exists

- **WHEN** `fold_old_messages` processes a message list where no agent message contains `tool_use` parts
- **THEN** the function falls back to keeping the most recent `LEGACY_RECENT_KEEP = 6` messages
- **AND** a warning is logged

### Requirement: tool_result replay SHALL differentiate by tool type

In `_render_agent_public_text`, the `TOOL_RESULT_REPLAY_CHAR_CAP = 4000` uniform truncation MUST be replaced with per-tool differentiation:
- `code_explore` results MUST NOT be truncated (preserved verbatim, regardless of length)
- `fs_read(mode="outline")` and `fs_read(mode="head")` results MUST NOT be truncated
- All other tool results MUST be truncated at `TOOL_RESULT_REPLAY_CHAR_CAP = 4000` characters with a `[truncated, N chars total]` suffix

The tool name and mode MUST be recovered by matching the `tool_result` part's `callId` to the corresponding `tool_use` part within the same message.

#### Scenario: code_explore result not truncated in replay

- **WHEN** `_render_agent_public_text` renders a `code_explore` `tool_result` part of 8000 characters
- **THEN** the full 8000 characters are included in the replay text
- **AND** no `[truncated]` suffix is appended

#### Scenario: bash result truncated in replay

- **WHEN** `_render_agent_public_text` renders a `bash` `tool_result` part of 6000 characters
- **THEN** only the first 4000 characters are included
- **AND** a `...[truncated, 6000 chars total]` suffix is appended
