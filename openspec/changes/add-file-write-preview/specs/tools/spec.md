# Delta Spec: tools

## MODIFIED Requirements

### Requirement: fs_write Tool Result Diff Data

The `fs_write` tool result SHALL include `path`, `oldContent`, and `newContent` fields in both `auto` and `review` approval modes.

#### Scenario: fs_write auto mode result

- **WHEN** `fs_write` executes in auto mode
- **THEN** the successful tool result SHALL include:
  - `path` (string): workspace-relative file path
  - `oldContent` (string | null): previous file content, or null if the file did not exist
  - `newContent` (string): the content that was written

#### Scenario: fs_write review mode result

- **WHEN** `fs_write` executes in review mode and the user approves
- **THEN** the successful tool result SHALL include:
  - `path` (string): workspace-relative file path
  - `oldContent` (string | null): previous file content before approval
  - `newContent` (string): the content that was approved and written

#### Scenario: fs_write review mode rejected

- **WHEN** `fs_write` review mode is rejected by the user
- **THEN** the tool result is an error (`User rejected the file change`), no diff data needed

### Requirement: fs_edit Tool Result Diff Data

The `fs_edit` tool result SHALL include `path`, `oldContent`, and `newContent` fields in both `auto` and `review` approval modes.

#### Scenario: fs_edit auto mode result

- **WHEN** `fs_edit` executes in auto mode
- **THEN** the successful tool result SHALL include:
  - `path` (string): workspace-relative file path
  - `oldContent` (string): the file content before the edit
  - `newContent` (string): the file content after the edit

#### Scenario: fs_edit review mode result

- **WHEN** `fs_edit` executes in review mode and the user approves
- **THEN** the successful tool result SHALL include:
  - `path` (string): workspace-relative file path
  - `oldContent` (string): the file content before the edit
  - `newContent` (string): the file content after the edit (the approved version)
