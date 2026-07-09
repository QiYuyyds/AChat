# Conversation Dispatch Mode

## Purpose

Defines the `dispatch_mode` field on `Conversation` and the routing logic that selects between Solo and Orchestrated agent loop execution.

## Requirements

### Requirement: Conversation SHALL have a `dispatch_mode` field

The `Conversation` model SHALL include a `dispatch_mode` column of type `VARCHAR` with allowed values `'solo'` and `'orchestrated'`, defaulting to `'solo'`.

#### Scenario: New conversation is created
- **WHEN** a conversation is created without specifying `dispatch_mode`
- **THEN** `dispatch_mode` defaults to `'solo'`.

#### Scenario: Conversation is in orchestrated mode
- **WHEN** `dispatch_mode = 'orchestrated'`
- **THEN** AgentRunner uses the orchestrated path with orchestrator agent and `TaskDispatch` tool.

#### Scenario: Conversation is in solo mode
- **WHEN** `dispatch_mode = 'solo'`
- **THEN** AgentRunner uses the solo loop path without orchestrator dispatch capabilities.

### Requirement: AgentRunner SHALL dispatch based on `dispatch_mode`

`AgentRunner.execute_run` SHALL branch on `conversation.dispatch_mode` to select the execution path.

#### Scenario: Solo mode execution
- **WHEN** `conversation.dispatch_mode = 'solo'`
- **THEN** AgentRunner invokes `run_agent_loop` with `mode='solo'`.
#### Scenario: Orchestrated mode execution
- **WHEN** `conversation.dispatch_mode = 'orchestrated'`
- **THEN** AgentRunner invokes `run_agent_loop` with `mode='coordinated'`.

### Requirement: `dispatch_mode` SHALL be mutable

Users SHALL be able to switch a conversation's `dispatch_mode` via the conversation settings UI or API.

#### Scenario: User switches from solo to orchestrated
- **WHEN** a user changes `dispatch_mode` from `'solo'` to `'orchestrated'`
- **THEN** subsequent messages use orchestrated loop
- **AND** previous messages are unaffected.

### Requirement: Backward compatibility SHALL be maintained

Code reading `conversation.dispatch_mode` SHALL default to `'solo'` if the column is missing or null (defensive for pre-migration data).

#### Scenario: Reading a conversation created before this change
- **WHEN** the `dispatch_mode` column is null
- **THEN** the application treats it as `'solo'`.
