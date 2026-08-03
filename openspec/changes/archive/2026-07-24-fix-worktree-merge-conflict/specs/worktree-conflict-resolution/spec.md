# Worktree Conflict Resolution

## ADDED Requirements

### Requirement: Merge-back SHALL attempt three-layer progressive conflict resolution

When `merge_worktree_back()` encounters a git merge conflict, it SHALL NOT immediately abort the merge. Instead, it SHALL progressively attempt: (1) standard git three-way merge (already attempted), (2) LLM-assisted conflict resolution, (3) human approval. Each layer SHALL only be invoked if the previous layer failed.

#### Scenario: Standard merge succeeds (Layer 1)

- **WHEN** `git merge --no-edit <branch>` returns 0
- **THEN** `MergeResult(success=True)` is returned
- **AND** no LLM call is made
- **AND** no human approval is requested

#### Scenario: LLM resolves conflict (Layer 2)

- **WHEN** `git merge` returns non-zero (conflict detected)
- **AND** conflict files contain `<<<<<<<` / `=======` / `>>>>>>>` markers
- **AND** LLM generates a merged version that passes syntax validation
- **THEN** the merged content is written to the file
- **AND** `git add <file>` is executed for each resolved file
- **AND** `git commit` completes the merge
- **AND** `MergeResult(success=True, resolution_strategy="llm")` is returned

#### Scenario: LLM fails syntax validation, falls back to human (Layer 3)

- **WHEN** LLM-generated content fails syntax validation
- **THEN** the merge conflict state is preserved (no `merge --abort`)
- **AND** three snapshots (base / ours / theirs) are saved as Artifacts
- **AND** a `MergeConflictPendingEvent` is published via SSE
- **AND** the merge-back coroutine blocks until the user resolves the conflict

#### Scenario: Human resolves conflict (Layer 3)

- **WHEN** the user selects "keep ours" for a conflicted file
- **THEN** `git checkout --ours <file>` is executed
- **AND** `git add <file>` is executed
- **AND** if all conflict files are resolved, `git commit` completes the merge
- **AND** `MergeResult(success=True, resolution_strategy="manual")` is returned

#### Scenario: Human abandons the task (Layer 3)

- **WHEN** the user selects "abandon this task"
- **THEN** `git merge --abort` is executed
- **AND** `MergeResult(success=False, resolution_strategy="abandoned")` is returned
- **AND** the worktree branch is preserved for potential manual recovery

### Requirement: LLM conflict resolution SHALL use only conflict file content

The LLM prompt for conflict resolution SHALL contain only the file path and the conflict marker content. It SHALL NOT include task descriptions, agent system prompts, or conversation context.

#### Scenario: LLM prompt construction

- **WHEN** Layer 2 is invoked for a conflicted file
- **THEN** the prompt contains the file path and the raw conflict content (including `<<<<<<<` / `=======` / `>>>>>>>` markers)
- **AND** the prompt instructs the LLM to output only the merged file content without explanation
- **AND** no task description or agent context is included

### Requirement: Syntax validation SHALL run after LLM merge

After the LLM generates merged content, the system SHALL validate syntax before accepting the merge. Validation rules: `.ts`/`.tsx`/`.js` files use bracket-pair matching; `.py` files use `compile()`; `.json` files use `json.loads()`; `.md` and other text files skip validation.

#### Scenario: Python file passes syntax check

- **WHEN** LLM generates merged content for a `.py` file
- **AND** `compile(content, filename, 'exec')` succeeds
- **THEN** the merged content is accepted and written to the file

#### Scenario: JSON file fails syntax check

- **WHEN** LLM generates merged content for a `.json` file
- **AND** `json.loads(content)` raises `JSONDecodeError`
- **THEN** the merged content is rejected
- **AND** the system falls back to Layer 3 (human approval)

### Requirement: Conflict snapshots SHALL be saved as Artifacts before human approval

When a conflict reaches Layer 3 (human approval), the system SHALL save three versions of each conflicted file as an Artifact of type `diff`: the common ancestor (base), the main workspace version (ours), and the worktree branch version (theirs).

#### Scenario: Snapshots saved

- **WHEN** a conflict is escalated to Layer 3
- **THEN** for each conflicted file, an Artifact is created with:
  - `type`: `"diff"`
  - `title`: `"合并冲突: {filename}"`
  - `content`: `{ base, ours, theirs, conflict_markers }`
- **AND** the Artifact is associated with the conversation

### Requirement: MergeResult SHALL include resolution metadata

The `MergeResult` dataclass SHALL include a `resolution_strategy` field indicating which layer resolved the conflict, and a `resolved_files` field listing files that were resolved by LLM or human.

#### Scenario: LLM resolution metadata

- **WHEN** Layer 2 successfully resolves all conflicts
- **THEN** `MergeResult.resolution_strategy == "llm"`
- **AND** `MergeResult.resolved_files` contains the list of files resolved by LLM

#### Scenario: No conflict metadata

- **WHEN** `git merge` succeeds without conflict (Layer 1)
- **THEN** `MergeResult.resolution_strategy == "auto"`
- **AND** `MergeResult.resolved_files` is empty

### Requirement: WorktreeEvent SHALL carry conflict details

The `WorktreeEvent` SSE event SHALL include `conflict_files` (list of conflicted file paths) and `resolution_status` (one of `"success"`, `"llm_resolved"`, `"manual_resolved"`, `"abandoned"`, `"conflict"`) when merge-back involves a conflict.

#### Scenario: Conflict detected event

- **WHEN** `git merge` detects a conflict
- **THEN** a `WorktreeEvent` with `type="worktree.merged"` is published
- **AND** `merge_status` is `"conflict"`
- **AND** `conflict_files` contains the list of conflicted file paths
- **AND** `resolution_status` is `"conflict"` (pending resolution)

#### Scenario: Conflict resolved event

- **WHEN** all conflicts are resolved (by LLM or human)
- **THEN** a `WorktreeEvent` with `type="worktree.merged"` is published
- **AND** `merge_status` is `"success"`
- **AND** `resolution_status` reflects the strategy used (`"llm_resolved"` or `"manual_resolved"`)

### Requirement: Human approval SHALL block indefinitely until user decides

The merge-back coroutine SHALL block until the user resolves the conflict or abandons the task. There SHALL be no timeout or automatic degradation. The blocking uses `asyncio.Event`, consistent with the existing `pending_writes` approval pattern.

#### Scenario: User not immediately available

- **WHEN** a conflict reaches Layer 3 and the user is not present
- **THEN** the merge-back coroutine blocks indefinitely
- **AND** the DAG executor waits for the merge to complete before proceeding
- **AND** subsequent waves in the DAG are not started until the conflict is resolved

#### Scenario: User resolves after delay

- **WHEN** the user resolves the conflict after some delay
- **THEN** the merge-back coroutine unblocks
- **AND** the DAG executor continues with the next wave
