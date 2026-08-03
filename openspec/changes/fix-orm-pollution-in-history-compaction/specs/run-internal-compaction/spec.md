## ADDED Requirements

### Requirement: Tool summarizer table SHALL cover all baseline and common optional tools

The `_SUMMARIZERS` dispatch table in `compact_pipeline.py` MUST include dedicated summarizers for all baseline agent tools (`fs_list`, `fs_read`, `fs_write`, `fs_edit`, `fs_grep`, `fs_glob`, `bash`, `read_attachment`, `ask_user`) and common optional tools (`write_artifact`, `read_artifact`, `update_artifact`, `deploy_artifact`, `deploy_workspace`, `web_search`, `load_skill`, `task_dispatch`, `dispatch_plan`, `create_plan`, `plan_step`, `add_plan_steps`, and the `manage_*` family). Tools not in the table MUST still fall back to the generic `_summarize_unknown` strategy, but the table SHOULD be comprehensive enough that the fallback is rarely hit in practice.

Each dedicated summarizer MUST extract semantically meaningful fields from the tool's output (e.g., `path` + `bytes_written` for `fs_write`, `artifactId` + `title` for `write_artifact`) rather than blindly truncating to a character limit. Each summarizer MUST produce a `(new_content, summary, recover_hint)` triple following the existing contract.

#### Scenario: load_skill result is summarized with skill name and description

- **WHEN** stage 1 prunes a `load_skill` `tool_result` whose content contains a skill definition with a name and description
- **THEN** the pruned result retains the skill name and the first 200 characters of the description
- **AND** the summary field indicates the skill name
- **AND** the recover hint suggests re-calling `load_skill` with the skill path

#### Scenario: write_artifact result is summarized with artifact ID and title

- **WHEN** stage 1 prunes a `write_artifact` `tool_result` whose content contains an artifact creation confirmation
- **THEN** the pruned result retains the `artifactId`, `title`, and `type` fields
- **AND** the summary field indicates the artifact title and type
- **AND** the recover hint suggests using `read_artifact` to re-fetch the content

#### Scenario: fs_write result is summarized with path and size

- **WHEN** stage 1 prunes a `fs_write` `tool_result` whose content contains a write confirmation
- **THEN** the pruned result retains the file path and bytes written
- **AND** the summary field indicates the path and size
- **AND** the recover hint suggests re-calling `fs_read` to retrieve the content

#### Scenario: web_search result is summarized with query and top results

- **WHEN** stage 1 prunes a `web_search` `tool_result` whose content contains search results
- **THEN** the pruned result retains the query and the first 5 result titles with URLs
- **AND** the summary field indicates the query and result count
- **AND** the recover hint suggests re-calling `web_search` with the same query

#### Scenario: Unknown tool still falls back to generic strategy

- **WHEN** a tool not in the `_SUMMARIZERS` table is encountered during pruning
- **THEN** the `_summarize_unknown` fallback is used (first 1000 chars at stage 1)
- **AND** a warning is logged so the team can identify tools that need dedicated summarizers
