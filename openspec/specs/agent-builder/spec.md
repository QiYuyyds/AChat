# Agent Builder

## Purpose

Defines how users create and edit non-orchestrator agents from the UI. Detailed behavior lives in `specs/10-agent-builder.md`.

## Requirements

### Requirement: User-created agents SHALL default to Custom adapter

New agents MUST default to `adapterName='custom'` unless the user selects Claude Code or Codex SDK adapter.

#### Scenario: User opens create dialog
- **WHEN** no existing agent is being edited
- **THEN** adapter kind defaults to Custom
- **AND** provider defaults to DeepSeek.

### Requirement: New custom agents SHALL start with an editable harness prompt

The create dialog MUST prefill `systemPrompt` with the coder role template that covers role positioning, production strategy, behavior constraints, and quality standards. The template SHALL NOT duplicate tool usage guidance (handled by layer-3 prompt) or plan/dispatch guidance (handled by layer-2 suffix).

#### Scenario: User opens create dialog
- **WHEN** no existing agent is being edited
- **THEN** the System Prompt field contains the coder role template
- **AND** the user can edit or replace it before saving.

### Requirement: Custom agents SHALL require provider and model

Custom agents MUST have `modelProvider` and a non-empty `modelId`; SDK agents SHALL ignore `modelProvider`.

#### Scenario: User clears custom model id
- **WHEN** adapter kind is Custom
- **THEN** form submission is rejected.

### Requirement: SDK agents SHALL use built-in tool sets

Claude Code and Codex agents MUST persist `toolNames=[]` because their tools come from the SDK runtime rather than AChat `toolRegistry`.

#### Scenario: User switches from Custom to Codex
- **WHEN** the form is submitted
- **THEN** the saved agent has no custom tool names.

### Requirement: Custom agents SHALL have baseline tools always enabled

Every custom adapter agent MUST have 9 baseline tools (`read_attachment`, `ask_user`, `fs_list`, `fs_read`, `fs_write`, `fs_edit`, `fs_grep`, `fs_glob`, `bash`) automatically merged at runtime by `agent_runner.py`. These tools are NOT selectable in the UI — they are displayed as a read-only hint. SDK agents (claude-code / codex) do NOT participate in baseline merge; they use CLI built-in tools.

#### Scenario: User creates a custom agent
- **WHEN** the create dialog opens for a Custom adapter agent
- **THEN** the tools tab shows a read-only baseline tools section listing all 9 tools
- **AND** the baseline tools are not checkboxes and cannot be toggled off.

#### Scenario: Old agent with baseline tools in toolNames
- **WHEN** an existing agent has baseline tools persisted in `toolNames`
- **THEN** runtime merge deduplicates them (order preserved, baseline first)
- **AND** the 5 UI-selectable tool selections remain unchanged.

### Requirement: Custom agents SHALL provide 4 role presets

The agent builder MUST provide one-click tool presets for 4 custom-agent roles: coder, researcher, orchestrator, and writer. Each preset binds a subset of the 5 UI-selectable tools and a `systemPromptTemplate` covering role positioning, production strategy, behavior constraints, and quality standards.

#### Scenario: User selects coder preset
- **WHEN** the user clicks the coder tool preset
- **THEN** the selected tools include `deploy_workspace` and `read_artifact`
- **AND** `write_artifact` is not selected unless the user adds it manually.

#### Scenario: User selects researcher preset
- **WHEN** the user clicks the researcher tool preset
- **THEN** the selected tools include `write_artifact`, `read_artifact`, and `web_search`.

#### Scenario: User creates a custom agent
- **WHEN** the create dialog opens for a Custom adapter agent
- **THEN** the default preset is coder
- **AND** `deploy_workspace` and `read_artifact` are selected.

### Requirement: Codex agent configuration SHALL reject unsupported base URLs

The agent builder MUST validate known unsupported Codex base URLs before saving or running the agent.

#### Scenario: DeepSeek URL is entered for Codex
- **WHEN** the Base URL host is `api.deepseek.com`
- **THEN** the UI shows a Codex/Responses compatibility error.

### Requirement: API key hints SHALL match adapter fallback

The UI MUST display key fallback hints that match AgentRunner's key resolution for selected adapter/provider.

#### Scenario: Codex key field is empty
- **WHEN** a Codex agent is saved without per-agent key
- **THEN** runtime falls back to app OpenAI key, `CODEX_API_KEY`, or `OPENAI_API_KEY`.
