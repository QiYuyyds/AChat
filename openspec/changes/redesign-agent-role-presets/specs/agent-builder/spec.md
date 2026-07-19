## MODIFIED Requirements

### Requirement: Custom agents SHALL provide tool presets

The agent builder MUST provide one-click tool presets for common custom-agent roles. Each preset MUST bind a differentiated system prompt template. The preset catalog MUST cover exactly four roles: `coder`, `researcher`, `orchestrator`, and `writer`. Presets apply only to Custom adapter agents; SDK agents (Claude Code, Codex) use their own built-in tool sets and are unaffected.

Each preset MUST define `{ id, label, desc, tools, systemPromptTemplate }`. The `tools` field contains only the subset of `AVAILABLE_AGENT_TOOLS` (the 5 UI-selectable tools); baseline tools are implicitly included for all custom agents and MUST NOT appear in preset `tools` lists. The `systemPromptTemplate` is a static, deterministic text covering four areas: role positioning, production strategy, behavior constraints, and quality standards. It MUST NOT include specific tool usage instructions (handled by `_build_agent_hub_tool_guidance`), multi-step plan guidance (handled by `_PLAN_SUFFIX`), or task dispatch guidance (handled by `_SOLO_DISPATCH_SUFFIX` / `_COORDINATED_PROMPT_SUFFIX`).

#### Scenario: User selects coder preset

- **WHEN** the user clicks the coder tool preset
- **THEN** the selected tools include `deploy_workspace` and `read_artifact`
- **AND** `write_artifact`, `deploy_artifact`, and `web_search` are not selected unless the user adds them manually
- **AND** the System Prompt is overwritten with the coder template
- **AND** baseline tools (fs_read, fs_write, fs_edit, fs_grep, fs_glob, fs_list, bash, ask_user, read_attachment) are not shown as checkboxes (they are implicitly always-on for custom agents).

#### Scenario: User selects researcher preset

- **WHEN** the user clicks the researcher tool preset
- **THEN** the selected tools include `write_artifact`, `read_artifact`, and `web_search`
- **AND** `deploy_artifact` and `deploy_workspace` are not selected unless the user adds them manually
- **AND** the System Prompt is overwritten with the researcher template.

#### Scenario: User selects orchestrator preset

- **WHEN** the user clicks the orchestrator tool preset
- **THEN** the selected tools include `write_artifact` and `read_artifact`
- **AND** `deploy_artifact`, `deploy_workspace`, and `web_search` are not selected unless the user adds them manually
- **AND** the System Prompt is overwritten with the orchestrator template.

#### Scenario: User selects writer preset

- **WHEN** the user clicks the writer tool preset
- **THEN** the selected tools include `write_artifact`, `deploy_artifact`, and `read_artifact`
- **AND** `deploy_workspace` and `web_search` are not selected unless the user adds them manually
- **AND** the System Prompt is overwritten with the writer template.

#### Scenario: User creates a custom agent

- **WHEN** the create dialog opens for a Custom adapter agent
- **THEN** the default preset is coder
- **AND** the System Prompt is prefilled with the coder template.

### Requirement: Custom agents SHALL have baseline tools always enabled

All Custom adapter agents MUST have the following baseline tools always enabled at runtime, regardless of the agent's persisted `toolNames`: `read_attachment`, `ask_user`, `fs_list`, `fs_read`, `fs_write`, `fs_edit`, `fs_grep`, `fs_glob`, `bash`. These tools MUST NOT appear in the UI tool checklist (they are not selectable). The runtime tool list for a Custom agent is computed as `BASELINE_AGENT_TOOLS ∪ agent.tool_names ∪ auto-injected tools` (deduplicated).

SDK agents (Claude Code, Codex) use their own CLI built-in tool sets and are unaffected by baseline tool logic.

#### Scenario: Custom agent with empty toolNames

- **WHEN** a Custom agent has `toolNames=[]`
- **THEN** at runtime the agent still has access to all 9 baseline tools
- **AND** the agent can call fs_read, fs_write, bash, ask_user, etc.

#### Scenario: Custom agent with toolNames containing baseline tools

- **WHEN** a Custom agent's persisted `toolNames` contains some baseline tool names (e.g., legacy agent created before baseline introduction)
- **THEN** at runtime the baseline tools are deduplicated (no duplicate entries)
- **AND** the agent's effective tool list includes baseline tools plus any non-baseline tools from `toolNames`.

#### Scenario: SDK agent ignores baseline tools

- **WHEN** a Claude Code or Codex agent is created
- **THEN** baseline tools are NOT injected (SDK agents use CLI built-in tools)
- **AND** the agent's `toolNames` is persisted as `[]`.

### Requirement: Role selection SHALL auto-overwrite tools and system prompt

Selecting a role preset MUST immediately switch the tool checklist to the preset's tool set (only the 5 UI-selectable tools) AND overwrite the System Prompt field with the preset's `systemPromptTemplate`. The user may manually adjust tools and prompt after the overwrite. Baseline tools are not affected by preset selection (they are always-on).

#### Scenario: User switches from coder to researcher

- **WHEN** the user clicks the researcher preset while the System Prompt contains the coder template
- **THEN** the tool checklist switches to the researcher tool set (`write_artifact`, `read_artifact`, `web_search`)
- **AND** the System Prompt is overwritten with the researcher template.

#### Scenario: Editing an existing agent

- **WHEN** the edit dialog opens for an existing Custom agent with a persisted `systemPrompt`
- **THEN** the initial active preset is inferred from the agent's persisted `toolNames` (matched against the 4 presets' tool sets)
- **AND** the persisted `systemPrompt` is NOT overwritten (only manual preset clicks overwrite).

### Requirement: System prompt template SHALL cover only role positioning, strategy, constraints, and quality

The `systemPromptTemplate` for each role MUST cover exactly four areas: (1) role positioning ("你是一名 X"), (2) production strategy (what to produce and how to deliver), (3) behavior constraints (role-specific "do / don't"), (4) quality standards (what makes output acceptable). It MUST NOT include specific tool usage instructions (e.g., "use fs_list with depth=3"), multi-step plan guidance (e.g., "create a plan for complex tasks"), or task dispatch guidance (e.g., "use task_dispatch to clone yourself"). These are handled by separate prompt layers (`_build_agent_hub_tool_guidance`, `_PLAN_SUFFIX`, `_SOLO_DISPATCH_SUFFIX`, `_COORDINATED_PROMPT_SUFFIX`).

#### Scenario: Coder template does not include tool usage instructions

- **WHEN** the coder `systemPromptTemplate` is rendered
- **THEN** it includes role positioning ("你是一名程序员"), production strategy (code changes land in workspace, deploy_workspace for previews), behavior constraints (read before edit, use fs_edit for precise changes), and quality standards (typecheck/build/test pass)
- **AND** it does NOT include instructions like "use fs_list with depth=3" or "call create_plan for multi-step tasks".

#### Scenario: Orchestrator template coexists with coordinated suffix

- **WHEN** an orchestrator agent runs in coordinated mode
- **THEN** the final system prompt is `workspace_info + systemPromptTemplate + _COORDINATED_PROMPT_SUFFIX + tool_guidance`
- **AND** the `systemPromptTemplate` covers role positioning (coordinator, priority dispatch, not self-execute) and aggregation quality
- **AND** the `_COORDINATED_PROMPT_SUFFIX` covers tool-level dispatch details (when to use dispatch_plan vs task_dispatch)
- **AND** there is no content overlap between the two layers.

### Requirement: Tool guidance SHALL only describe tools the agent actually has

The `_build_agent_hub_tool_guidance` function in `agent_runner.py` MUST only describe tools that are in the agent's effective tool list. The `has_file_tools` block MUST NOT unconditionally output descriptions and "correct examples" for `fs_write` / `fs_edit` / `bash` when the agent does not have those tools. Each tool description line in the file tools block MUST be gated by a check that the tool is in the agent's effective tool set.

#### Scenario: Agent without fs_write

- **WHEN** an agent's effective tool list does not include `fs_write`
- **THEN** the tool guidance block does NOT output "fs_write / fs_edit：写入新文件..." or "fs_write 正确案例：..."
- **AND** only tools the agent actually has are described.

#### Scenario: Agent with all baseline tools

- **WHEN** a Custom agent has all baseline tools (the default after this change)
- **THEN** the tool guidance block describes fs_list, fs_read, fs_write, fs_edit, fs_grep, fs_glob, bash as applicable
- **AND** each description is gated by the tool's presence in the effective tool list.
