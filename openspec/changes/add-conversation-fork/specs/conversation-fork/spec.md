## ADDED Requirements

### Requirement: System SHALL support non-destructive conversation fork

The system MUST provide a `fork_conversation` API that creates a new conversation by deep-copying all messages (where `hidden=false`) from the source conversation up to and including the specified fork-point message. The source conversation MUST remain completely unmodified. The new conversation MUST have its own independent Workspace, Messages, and Artifacts.

#### Scenario: Fork from a completed agent message

- **WHEN** a user calls `POST /api/conversations/{conv_a}/fork` with `forkPointMessageId` pointing to an agent message whose run has ended (`status` in `complete`, `error`, `aborted`)
- **THEN** a new conversation `conv_b` is created with `parent_conversation_id = conv_a` and `fork_point_message_id = <specified message>`
- **AND** all messages with `hidden=false` and `created_at <= fork_point.created_at` are deep-copied to `conv_b` with new message IDs
- **AND** all Artifacts belonging to `conv_a` that were created at or before the fork point are deep-copied to `conv_b` with new Artifact IDs
- **AND** `conv_b` receives its own Workspace row (1:1 with conversation)
- **AND** `conv_b.agent_ids` equals `conv_a.agent_ids`
- **AND** `conv_b.mode` equals `conv_a.mode`
- **AND** `conv_b.dispatch_mode` equals `conv_a.dispatch_mode`
- **AND** the source conversation `conv_a` is not modified in any way
- **AND** the response returns the new conversation record in the same format as `POST /api/conversations`

#### Scenario: Fork from a user message

- **WHEN** the `forkPointMessageId` points to a user message
- **THEN** the fork proceeds identically, copying all messages up to and including that user message
- **AND** no agent run is triggered in the new conversation — the user starts fresh

#### Scenario: Fork from a streaming message

- **WHEN** the `forkPointMessageId` points to a message with `status = 'streaming'`
- **THEN** the API returns `400` with error `"Cannot fork from a message that is still streaming"`

#### Scenario: Fork from a non-existent message

- **WHEN** the `forkPointMessageId` does not exist in the source conversation
- **THEN** the API returns `404` with error `"Message not found"`

#### Scenario: Hidden messages are excluded from fork

- **WHEN** the source conversation has messages with `hidden=true` (clone-subagent messages)
- **THEN** those hidden messages are NOT copied to the forked conversation
- **AND** only `hidden=false` messages are deep-copied

### Requirement: Forked workspace SHALL be isolated via git worktree

The forked conversation's workspace MUST be an isolated git worktree (or directory copy fallback) of the source workspace's effective cwd. The worktree MUST persist for the lifetime of the forked conversation and MUST NOT be merged back automatically.

#### Scenario: Source workspace is a git repository

- **WHEN** the source workspace's effective cwd is already a git repository
- **THEN** a git worktree is created via `git worktree add -b fork/{conv_b_id} <target_path> HEAD`
- **AND** the target path is under `.agenthub-data/workspaces/users/{user_id}/{conv_b_id}/`
- **AND** the new workspace `mode` is `"sandbox"`, `bound_path` is `null`
- **AND** the worktree branch is named `fork/{conv_b_id}`

#### Scenario: Source workspace is not a git repository

- **WHEN** the source workspace's effective cwd is not a git repository
- **AND** `confirmGitInit` is `true` in the request
- **THEN** `ensure_git_init()` is called on the source directory first
- **AND** a smart `.gitignore` is written (if none exists) with common ignore patterns
- **AND** then a git worktree is created as above

#### Scenario: Source workspace is not git and user does not confirm

- **WHEN** the source workspace's effective cwd is not a git repository
- **AND** `confirmGitInit` is `false` or absent in the request
- **THEN** the API returns `409` with error `"Git initialization required"` and includes `{"requiresGitInit": true, "sourcePath": "<path>"}`
- **AND** no conversation or workspace is created

#### Scenario: Worktree creation fails

- **WHEN** `git worktree add` fails (e.g., disk full, git error)
- **THEN** the API returns `500` with error `"Failed to create fork workspace"`
- **AND** the partially created conversation is rolled back (conversation + workspace rows deleted)
- **AND** a warning is logged

### Requirement: Forked conversation SHALL display origin info

The forked conversation MUST carry metadata that allows the frontend to display where it was forked from.

#### Scenario: Fork metadata on conversation record

- **WHEN** the forked conversation record is returned from the API
- **THEN** it includes `parentConversationId` (the source conversation ID)
- **AND** it includes `forkPointMessageId` (the message ID in the source conversation that was the fork point)
- **AND** the conversation title is `"{original_title} (分支)"` or `"{original_title} (分支 {N})"` if a fork from the same source already exists

### Requirement: Deleting a forked conversation SHALL clean up its worktree

When a forked conversation is deleted, its git worktree and branch MUST be cleaned up. The source conversation MUST NOT be affected when a fork is deleted.

#### Scenario: Delete forked conversation

- **WHEN** a conversation with `parent_conversation_id IS NOT NULL` is deleted
- **THEN** its git worktree is removed (`git worktree remove --force`)
- **AND** its git branch is deleted (`git branch -D fork/{conv_id}`)
- **AND** the source conversation is not affected

#### Scenario: Delete source conversation after fork exists

- **WHEN** a conversation that has been forked from is deleted
- **THEN** the forked conversation is NOT deleted (no CASCADE)
- **AND** `parent_conversation_id` in the forked conversation remains as-is (historical reference, no FK enforcement)
- **AND** the forked conversation's worktree is unaffected (worktrees are independent git directories)

### Requirement: Fork SHALL preserve conversation configuration

The forked conversation MUST inherit the source conversation's agent roster, mode, dispatch mode, and approval settings.

#### Scenario: Group chat fork

- **WHEN** a group conversation with agents `[orchestrator, coder, writer]` is forked
- **THEN** the forked conversation has the same `agent_ids = [orchestrator, coder, writer]`
- **AND** `mode = "group"`
- **AND** `dispatch_mode` matches the source
- **AND** `fs_write_approval_mode` matches the source
