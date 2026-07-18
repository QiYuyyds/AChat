## MODIFIED Requirements

### Requirement: fs_list SHALL support recursive directory depth

`fs_list` MUST accept an optional `depth` integer parameter (range 1–5, default 1). When `depth=1`, the tool behaves exactly as before (lists entries in a single directory). When `depth>1`, the tool recursively expands subdirectories up to the specified depth, returning a flat list of entries where each entry includes a `relativePath` field (path relative to the workspace cwd) and a `depth` field (1-based depth from the requested path). Recursive expansion MUST skip dependency directories (`node_modules`, `.git`, `.venv`, `__pycache__`, `.next`, `dist`, `build`). The total number of entries returned MUST be capped at 500; when exceeded, the response MUST include `"truncated": true`.

#### Scenario: Agent requests project structure overview
- **WHEN** `fs_list` is called with `path=""` and `depth=3`
- **THEN** the tool returns a flat list of entries covering directories and files up to 3 levels deep
- **AND** each entry includes `relativePath` (e.g. `"src/components/Chat.tsx"`) and `depth` (1, 2, or 3)
- **AND** entries inside `node_modules` / `.git` / `dist` / `build` are excluded from recursive expansion

#### Scenario: depth=1 preserves current behavior
- **WHEN** `fs_list` is called without `depth` or with `depth=1`
- **THEN** the response is identical to the current behavior (entries from a single directory, no `relativePath` or `depth` fields)

#### Scenario: Large project exceeds entry cap
- **WHEN** `fs_list` with `depth>1` would return more than 500 entries
- **THEN** the response includes only the first 500 entries
- **AND** `"truncated": true` is set in the response

### Requirement: fs_list SHALL support showing hidden files

`fs_list` MUST accept an optional `showHidden` boolean parameter (default `false`). When `showHidden=false`, dotfiles (entries whose name starts with `.`) are hidden, matching current behavior. When `showHidden=true`, dotfiles are included in the listing. This applies to both `depth=1` and `depth>1` modes.

#### Scenario: Agent reads project config files
- **WHEN** `fs_list` is called with `showHidden=true`
- **THEN** entries such as `.env.example`, `.eslintrc.json`, `.gitignore` are included in the response

#### Scenario: Default hides dotfiles
- **WHEN** `fs_list` is called without `showHidden` or with `showHidden=false`
- **THEN** entries whose name starts with `.` are excluded from the response

### Requirement: fs_read SHALL support outline mode

`fs_read` MUST accept an optional `mode` parameter with values `"full"`, `"outline"`, or `"head"` (default `"full"`). When `mode="outline"`, the tool extracts and returns only the structural skeleton of the file: import statements, type/class/interface/enum definitions, function/method signatures with parameters, and top-level variable declarations. Extraction MUST be performed using regular expressions without invoking an LLM. The response MUST include `mode`, `language` (detected from file extension), `outline` (array of structural items with `type`, `line`, and `content`), `totalLines`, and `fullSize`. When no structural elements are detected, the response MUST include an empty `outline` array and a note suggesting `mode="full"`.

#### Scenario: Agent scans file structure without reading full content
- **WHEN** `fs_read` is called with `mode="outline"` on a 234-line TypeScript file
- **THEN** the response contains an `outline` array with import statements, function signatures, and top-level variable declarations
- **AND** the response does NOT include the full `content` field
- **AND** `totalLines` and `fullSize` are provided so the agent can decide whether to do a full read

#### Scenario: Outline detects no structure
- **WHEN** `fs_read` with `mode="outline"` is called on a file with no recognizable structural patterns (e.g. a plain text config)
- **THEN** the response includes an empty `outline` array
- **AND** a `note` field suggests the agent retry with `mode="full"`

### Requirement: fs_read SHALL support head mode

When `mode="head"`, `fs_read` MUST return only the first N lines of the file, where N is the `limit` parameter (default 50 if not specified). The response MUST include `content` (the first N lines), `startLine` (1), `endLine` (N or totalLines if fewer), `totalLines`, and `truncated` (true when the file has more lines than N).

#### Scenario: Agent previews file beginning
- **WHEN** `fs_read` is called with `mode="head"` on a 234-line file
- **THEN** the response contains the first 50 lines (or the `limit` value if specified)
- **AND** `truncated=true` and `totalLines=234` are included

### Requirement: fs_read mode=full preserves current behavior

When `mode="full"` (the default), `fs_read` MUST behave exactly as before: return the complete file content (up to 50,000 characters), with optional `offset`/`limit` pagination. The response format MUST be identical to the current implementation.

#### Scenario: Default full read
- **WHEN** `fs_read` is called without `mode` or with `mode="full"`
- **THEN** the response includes the full `content` field as before
- **AND** the `outline` field is NOT included in the response

### Requirement: code_explore SHALL auto-initialize on project binding

When a local-mode workspace is created or bound to a project path, AChat MUST automatically trigger `schedule_workspace_enable()` in the background to begin building the code intelligence graph. This initialization MUST be asynchronous and MUST NOT block the workspace creation API response. The auto-initialization is unconditional for all local-mode workspaces — the deprecated `codeIntelligenceEnabled` request parameter MUST be accepted for backward compatibility but MUST NOT affect the auto-trigger behavior. If code intelligence is disabled via global application settings, the auto-initialization MUST be skipped.

#### Scenario: User binds a local project folder
- **WHEN** a workspace is created with `mode="local"` and a `boundPath`
- **THEN** AChat triggers `schedule_workspace_enable()` in the background
- **AND** the workspace creation API responds immediately without waiting for graph readiness

#### Scenario: Deprecated codeIntelligenceEnabled flag is ignored
- **WHEN** a workspace is created with `mode="local"`, a `boundPath`, and `codeIntelligenceEnabled=false`
- **THEN** AChat still triggers `schedule_workspace_enable()` in the background
- **AND** the deprecated flag has no effect on auto-initialization

#### Scenario: Code intelligence disabled in settings
- **WHEN** code intelligence is disabled via application settings
- **AND** a local workspace is bound
- **THEN** auto-initialization is skipped
- **AND** no error is raised

### Requirement: code_explore fallback SHALL provide actionable guidance

When `code_explore` is unavailable (graph not ready or disabled), the fallback error message MUST include: (1) the current graph status, (2) concrete alternative tool suggestions appropriate for project exploration (e.g. `fs_list` with `depth>1`, `fs_grep` for symbol search, `fs_read` with `mode="outline"` for file structure), and (3) a note that `code_explore` will become available once the graph is ready.

#### Scenario: Agent calls code_explore before graph is ready
- **WHEN** `code_explore` is called and the graph status is `"indexing"`
- **THEN** the fallback message states the current status
- **AND** suggests using `fs_list(depth=3)` for project structure, `fs_grep` for symbol search, and `fs_read(mode="outline")` for file skeletons
- **AND** notes that `code_explore` will be available after indexing completes
