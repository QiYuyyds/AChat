# Capability: Run-Internal Compaction

## Purpose

Defines the in-memory message compaction strategy for the SDK ReAct loop (`_run_react_loop`). This capability covers when to trigger compaction (escalating ratio thresholds), how to prune (per-tool summarization strategies), what to keep (turn-boundary-aware retention), how to judge success (strict token reduction), and the marker format for pruned/folded content. This is **distinct** from `conversation-context` (spec 13), which handles cross-run DB message → LLM history serialization. Run-internal compaction only modifies the in-memory `messages[]` array within a single `_run_react_loop`; it never writes to DB or alters `ToolResultEvent` persistence.

---

## Requirements

### Requirement: ReAct loop SHALL use a five-stage compaction pipeline

The SDK ReAct loop (`_run_react_loop`) MUST replace the single-point `_mid_run_compact` with a five-stage pipeline triggered by escalating `ratio` thresholds (total_tokens / model_limit). Stage 1 (semantic summarization) MUST trigger at `ratio ≥ 0.70`; stage 2 (moderate pruning) at `ratio ≥ 0.80`; stage 3 (turn-boundary folding) at `ratio ≥ 0.88`. Stage 4 (soft wrap-up injection) and stage 5 (forced final with tools cleared) MUST preserve their current behavior at `ratio ≥ 0.93` and `ratio ≥ 0.95` respectively. Stages 1/2/3 MUST NOT invoke an LLM (pure regex extraction + structural pruning). The pipeline MUST be idempotent within a single model-call iteration: after a stage runs, `decide_pre_model` MUST be re-evaluated before the next action.

#### Scenario: Long exploration run triggers stage 1 early

- **WHEN** an 8-turn × 7-tool run reaches 70% of the model context window (ratio = 0.70) at turn 6
- **THEN** stage 1 (semantic summarization) triggers before the next model call
- **AND** stage 1 only prunes `tool_result` messages older than the most recent 2 complete turns
- **AND** the model still sees the most recent 2 turns of tool_use + tool_result pairs intact
- **AND** no LLM is invoked during stage 1 (regex-only extraction)

#### Scenario: Pipeline escalates through stages under sustained pressure

- **WHEN** after stage 1 completes, the next model turn produces more tool calls and `ratio` climbs back to 0.80
- **THEN** stage 2 (moderate pruning) triggers, re-pruning the stage-1 summaries
- **AND** if `ratio` still climbs to 0.88, stage 3 (turn folding) triggers, folding older turns into a single marker while keeping the most recent 2 complete turns intact
- **AND** stage 4 (soft wrap-up) and stage 5 (forced final) remain available as the final escalation path

### Requirement: Tool result summarization SHALL be differentiated by tool name and mode

Stage 1/2 pruning MUST apply a per-tool retention strategy based on the `tool_name` and tool-call arguments. The retention table MUST cover at least: `fs_list` (differentiated by `depth`), `fs_read` (differentiated by `mode`), `fs_grep`, `bash`, and `code_explore`. Tools not in the table MUST fall back to a generic "first 1k chars → marker → folded" strategy. `code_explore` output MUST be preserved verbatim across all stages (it is already a high-density summary). `fs_read(mode="outline")` and `fs_read(mode="head")` MUST be preserved verbatim across all stages (they are already short).

#### Scenario: fs_list depth>1 result keeps entry names and relative paths at stage 1

- **WHEN** stage 1 prunes a `fs_list` `tool_result` whose original call had `depth > 1`
- **THEN** the pruned result retains each entry's `name` and `relativePath` fields
- **AND** the `size`, `depth`, and `isDirectory` fields are dropped from each entry
- **AND** the pruned result is no larger than 60% of the original token count

#### Scenario: fs_read full result converts to outline at stage 1

- **WHEN** stage 1 prunes a `fs_read` `tool_result` whose original call had `mode="full"`
- **THEN** the pruned result calls `extract_outline(content, language)` (existing helper in `fs_service.py`) to produce a structural skeleton
- **AND** the pruned result includes `outline`, `language`, `totalLines`, `fullSize` fields
- **AND** the full `content` field is dropped
- **AND** if `extract_outline` returns an empty array, the pruned result includes a `note` suggesting the model re-call `fs_read(mode="full")` to recover

#### Scenario: code_explore result is never pruned

- **WHEN** any stage (1, 2, or 3) encounters a `code_explore` `tool_result`
- **THEN** the result is preserved verbatim, regardless of its token size
- **AND** no marker is substituted

### Requirement: Message folding SHALL respect turn boundaries

Stage 3 folding MUST use a `TurnBoundaryFinder` to identify complete turns (one `assistant` message containing `tool_calls` followed by all consecutive `tool` messages). Folding MUST keep the most recent `KEEP_RECENT_TURNS = 2` complete turns intact and replace older turns with a single fold marker. Folding MUST NOT split a `tool_use ↔ tool_result` pair across the fold boundary. If no complete turn boundary is found (e.g., all assistant messages lack `tool_calls`), the stage MUST fall back to the legacy message-count strategy (`recent_keep = 6`) and log a warning.

#### Scenario: Folding keeps recent turns intact

- **WHEN** stage 3 triggers on a messages list with 4 complete turns
- **THEN** the most recent 2 complete turns are kept verbatim in the messages list
- **AND** the older 2 turns are replaced with a single fold marker
- **AND** no `tool_use` message is separated from its corresponding `tool_result` message

### Requirement: Compaction markers SHALL carry recovery metadata

Fold markers and `tool_result` substitution markers MUST be structured plain text (not JSON) and MUST include: the `stage` that produced the marker, the `tool` name (or `tools_used` summary for fold markers), a `summary` field (≤ 200 chars), and a `recover` hint telling the model how to re-fetch the original information. Single markers MUST NOT exceed 500 characters.

#### Scenario: Fold marker lists tools used and recovery hint

- **WHEN** stage 3 folds 3 turns that collectively used `fs_list` × 2, `fs_read` × 5, and `bash` × 1
- **THEN** the fold marker includes a `tools_used` field listing at most the top 5 tools with counts
- **AND** the marker includes a `summary` field of at most 200 characters describing the folded segment
- **AND** the marker total length does not exceed 500 characters

#### Scenario: Tool result marker includes recover hint

- **WHEN** stage 1 prunes a `fs_list` `tool_result` whose original call had `path="src"` and `depth=3`
- **THEN** the substitution marker includes a `recover` field suggesting `fs_list(path='src', depth=3)` to re-fetch the structure
- **AND** the marker includes a `summary` field with a short description of what the original result contained

### Requirement: Compaction pipeline SHALL be toggleable via settings

A new setting `compact_pipeline_enabled` (default `True`) MUST be available in application settings. When `False`, the ReAct loop MUST fall back to the legacy `_mid_run_compact` behavior (single-point 0.90 trigger, `recent_keep=6`, JSON-based token estimation, len-based success). This toggle exists for rollback safety in production and MUST NOT affect stages 4/5 (soft wrap-up / forced final).

#### Scenario: Rollback to legacy compaction

- **WHEN** `compact_pipeline_enabled = False` is set in application settings
- **THEN** the ReAct loop uses the legacy `_mid_run_compact` function with `recent_keep = 6`
- **AND** token estimation uses `estimate_tokens(json.dumps(messages))`
- **AND** success judgment uses the legacy `post_tokens < pre_tokens or len(messages) < pre_compact_count` rule
- **AND** stages 4/5 (soft wrap-up / forced final) still trigger at 0.93 / 0.95 as before

### Requirement: Sub-agent runs SHALL inherit the same pipeline

`spawn_subagent_loop` (which also routes through `_run_react_loop`) MUST use the same five-stage pipeline and the same thresholds as top-level runs. No sub-agent-specific tuning is applied in this revision. If sub-agent runs exhibit premature compaction in production, a future change MAY introduce a `subagent_compact_ratio_override` setting.

#### Scenario: Sub-agent run uses same compaction as parent

- **WHEN** a sub-agent is dispatched via `task_dispatch` and its `_run_react_loop` reaches `ratio ≥ 0.70`
- **THEN** stage 1 triggers with the same retention strategies as for top-level runs
- **AND** the same `KEEP_RECENT_TURNS = 2` applies
- **AND** no sub-agent-specific threshold override is consulted
