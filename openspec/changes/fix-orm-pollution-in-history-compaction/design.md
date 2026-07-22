# Design: Fix ORM Pollution in History Compaction

## Context

`build_history_for` is a read-only operation: it serializes a conversation's `Message` history into OpenAI-format chat dicts for `AdapterInput.history`. As part of cross-run context compaction, `prune_old_tool_results` replaces large `tool_result` parts in old messages with compact markers.

The current implementation has a critical bug: it writes the compacted parts back to the ORM `Message` object (`msg.parts_list = parts`), and the `get_local_db()` context manager auto-commits on exit. This permanently overwrites the original `tool_result` content in the database. Users see compression markers instead of real tool outputs after a page refresh.

The `prune_old_tool_results` docstring explicitly warns against this:

> IMPORTANT: this function creates deep copies of parts before modifying them to avoid mutating the ORM objects attached to the session. Writing compact markers back to the DB would permanently corrupt the message history seen by the frontend after a page refresh.

But the implementation contradicts the docstring: `copy.deepcopy` only prevents in-place mutation of the returned list, then `msg.parts_list = parts` re-assigns the modified copy back to the ORM object, making it dirty.

A secondary issue: `_SUMMARIZERS` in `compact_pipeline.py` only covers 5 tools (`fs_list`, `fs_read`, `bash`, `fs_grep`, `code_explore`). All other tools (`load_skill`, `write_artifact`, `fs_write`, `fs_edit`, `fs_glob`, `web_search`, `read_artifact`, `read_attachment`, etc.) fall back to `_summarize_unknown`, which blindly truncates to 1000 chars with "未知工具结果" — even when those tools have structured, summarizable output.

## Goals / Non-Goals

**Goals:**

- Prevent `prune_old_tool_results` from mutating persisted `Message` records under any code path
- Expand tool summarizer coverage to all baseline and common optional tools so compaction produces meaningful summaries
- Add a regression test that asserts DB immutability after `build_history_for`

**Non-Goals:**

- Redesigning the compaction pipeline architecture (five-stage pipeline stays as-is)
- Changing the `get_local_db()` auto-commit behavior (it's used widely; changing it risks unintended side effects)
- Adding read-only session support (architecturally desirable but out of scope for this fix)
- Modifying the in-memory ReAct loop compaction (`run_compact_pipeline` operates on plain dicts, not ORM objects — no bug there)

## Decisions

### Decision 1: Detach ORM objects via `db.expunge_all()` after loading

**Choice**: In `_build_history_legacy`, call `db.expunge_all()` immediately after loading `recent` and `pinned` Message objects, before any compaction logic runs.

**Rationale**: `expunge_all()` removes all objects from the session's identity map, making them detached. Subsequent attribute assignments (`msg.parts_list = parts`) won't mark them dirty, and `session.commit()` won't flush them. This is a one-line fix at the call site, not a change to `prune_old_tool_results` itself.

**Alternatives considered**:

- *Alternative A: Use `make_transient()` per object* — More surgical but requires iterating over every loaded message. `expunge_all()` is simpler and the `_build_history_legacy` function already loads all needed objects in one block.

- *Alternative B: Change `prune_old_tool_results` to not write back to `msg.parts_list`* — Would require returning a parallel data structure (e.g., `dict[msg_id, list[parts]]`) and threading it through `fold_old_messages` and `_serialize_message`. Much larger change, higher risk of introducing bugs in the compaction logic.

- *Alternative C: Use read-only session (no auto-commit)* — Architecturally cleanest but requires adding a new context manager to `engine.py` and auditing all callers. Out of scope for a bugfix.

### Decision 2: Expand `_SUMMARIZERS` with lightweight per-tool strategies

**Choice**: Add dedicated summarizers for the following tools, organized by output type:

| Tool | Strategy (stage 1) | Rationale |
|---|---|---|
| `load_skill` | Keep skill name + first 200 chars of description | Output is a skill definition; name + purpose is enough context |
| `write_artifact` | Keep artifactId + title + type | Output is a creation confirmation; ID + type is the key info |
| `read_artifact` | Keep artifactId + title + first 500 chars of content | Output is artifact content; preserve enough to know what it was |
| `update_artifact` | Keep artifactId + version + summary line | Output is a version bump; version number is key |
| `fs_write` | Keep path + bytes written | Output is a write confirmation; path + size is the key info |
| `fs_edit` | Keep path + lines changed | Output is an edit confirmation; path + scope is key |
| `fs_glob` | Keep pattern + first 10 matches | Output is a file list; pattern + sample is enough |
| `web_search` | Keep query + first 5 result titles + URLs | Output is search results; titles are the key signal |
| `read_attachment` | Keep fileName + first 500 chars | Output is attachment content; name + preview is enough |
| `deploy_artifact` / `deploy_workspace` | Keep deployment status + preview URL | Output is a deploy result; status + URL is key |
| `task_dispatch` | Keep agentId + status + result head (200 chars) | Output is a sub-agent result; status + summary is key |
| `dispatch_plan` | Keep task count + statuses summary | Output is a batch dispatch; aggregate status is key |
| `create_plan` / `plan_step` / `add_plan_steps` | Keep planId + step count | Output is plan management; plan ID + structure is key |
| `manage_*` (7 tools) | Keep action + result status | Output is management result; action + status is key |
| `ask_user` | Keep question + answer head (200 chars) | Output is user response; the answer is key |

**Rationale**: Each tool has a distinct output shape. A dedicated summarizer extracts the semantically important fields rather than blindly truncating. This improves both the LLM's context quality and the recover hint accuracy.

**Alternatives considered**:

- *Alternative A: Generic JSON key-extraction for all unknown tools* — Would work for structured JSON but not for plain-text outputs (like `bash`). Per-tool strategies are more precise.

- *Alternative B: Leave unknown tools as-is (no truncation)* — Would defeat the purpose of compaction for large tool outputs.

### Decision 3: Regression test approach

**Choice**: Create a test that:
1. Seeds a conversation with messages containing `tool_result` parts for multiple tool types
2. Calls `build_history_for` (which internally calls `prune_old_tool_results`)
3. Re-reads the messages from DB
4. Asserts that `Message.parts` in DB still contains the original `tool_result` content (not compact markers)

**Rationale**: This directly tests the bug scenario and will catch any future regression where compaction logic accidentally writes back to ORM objects.

## Risks / Trade-offs

- **[Risk: `expunge_all()` detaches objects that downstream code might expect to be attached]** → Mitigation: After `expunge_all()`, no code in `_build_history_legacy` performs DB queries on the detached objects. All DB access (agent name map, artifact titles) happens before expunge or uses separate queries. The `_build_history_with_assembler` path doesn't call `prune_old_tool_results` at all (it delegates to PromptAssembler), so it's unaffected.

- **[Risk: New summarizers might produce larger output than original for small results]** → Mitigation: `prune_old_tool_results` already has a safeguard: `if new_content != content and len(new_content) < len(content)`. The same guard applies to all summarizers.

- **[Risk: Test might be flaky if it depends on specific DB state]** → Mitigation: Test creates its own conversation and messages in a test DB, runs the assertion, then cleans up. No dependency on pre-existing data.
