## 1. Database Schema & Migration

- [x] 1.1 Add `parent_conversation_id: Mapped[str | None]` and `fork_point_message_id: Mapped[str | None]` columns to `Conversation` model in `backend/app/db/models.py` (both nullable, no FK — historical reference only)
- [x] 1.2 Add `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS parent_conversation_id TEXT` and `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS fork_point_message_id TEXT` to `backend/app/db/engine.py` startup migration
- [x] 1.3 Add `parentConversationId` and `forkPointMessageId` fields to `ConversationResponse` / conversation Pydantic schema (camelCase alias, optional/nullable)
- [x] 1.4 Sync `src/db/schema.ts` conversations table definition with the two new columns (for frontend type compatibility)
- [x] 1.5 Sync `src/shared/types.ts` `ConversationWithMeta` type with the two new fields

## 2. Backend: Worktree Enhancement

- [x] 2.1 Enhance `ensure_git_init()` in `backend/app/services/worktree_service.py` to write a smart `.gitignore` when none exists — include common ignore patterns: `node_modules/`, `__pycache__/`, `*.pyc`, `dist/`, `build/`, `.next/`, `out/`, `target/`, `.env`, `.env.*`, `!.env.example`, `.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db`, `*.log`, `.agenthub-data/`
- [x] 2.2 When user already has `.gitignore`, `ensure_git_init()` should only append `.agenthub-data/` line (if not already present) instead of overwriting
- [x] 2.3 Add `create_fork_worktree(source_workspace_path, fork_conv_id, user_id) -> WorktreeRef | None` function — creates a persistent git worktree under `.agenthub-data/workspaces/users/{uid}/{fork_conv_id}/` with branch name `fork/{fork_conv_id}`. Does NOT call merge-back or cleanup (unlike DAG worktree)
- [x] 2.4 Add `cleanup_fork_worktree(workspace_path, branch_name)` function — removes worktree (`git worktree remove --force`) and deletes branch (`git branch -D`) — called when a forked conversation is deleted

## 3. Backend: Fork Conversation Service

- [x] 3.1 Implement `fork_conversation(source_conv_id, fork_point_message_id, user_id, confirm_git_init=False)` in `backend/app/services/conversation_service.py`:
  - Load source conversation + workspace
  - Validate fork point message exists and is not streaming
  - Determine source workspace effective cwd (sandbox: `root_path`; local: `bound_path`)
  - If not git repo and `confirm_git_init=False`: raise `GitInitRequiredError` with `source_path`
  - If not git repo and `confirm_git_init=True`: call `ensure_git_init(source_cwd)`
  - Call `create_fork_worktree(source_cwd, new_conv_id, user_id)` → get worktree path
  - Create new Conversation row (copy `agent_ids`, `mode`, `dispatch_mode`, `fs_write_approval_mode`; set `parent_conversation_id`, `fork_point_message_id`; title = `{source_title} (分支)` or `(分支 {N})`)
  - Create new Workspace row (`mode='sandbox'`, `root_path=worktree_path`, `bound_path=null`)
  - Deep-copy Messages: `SELECT * FROM messages WHERE conversation_id=source AND hidden=false AND created_at <= fork_point.created_at ORDER BY created_at` → insert with new IDs and new `conversation_id`
  - Deep-copy Artifacts: `SELECT * FROM artifacts WHERE conversation_id=source AND created_at <= fork_point.created_at` → insert with new IDs, new `conversation_id`, `version=1`, `parent_artifact_id=null` (version chain resets)
  - Return `ConversationResponse` for the new conversation
- [x] 3.2 Add `GitInitRequiredError` exception class (carries `source_path` attribute) or use a dataclass result that the API layer translates to `409`
- [x] 3.3 Handle fork title deduplication: if a conversation with title `"{source_title} (分支)"` already exists, try `"{source_title} (分支 2)"`, `"{source_title} (分支 3)"`, etc.
- [x] 3.4 Handle rollback: if worktree creation or message copy fails after conversation row is created, delete the conversation + workspace rows and clean up any partial worktree

## 4. Backend: API Endpoint

- [x] 4.1 Add `ForkConversationRequest` Pydantic model to `backend/app/schemas/requests.py`: `fork_point_message_id: str = Field(alias="forkPointMessageId")`, `confirm_git_init: bool = Field(default=False, alias="confirmGitInit")`
- [x] 4.2 Add `POST /api/conversations/{conversation_id}/fork` route to `backend/app/api/conversations.py`:
  - Verify conversation ownership
  - Parse `ForkConversationRequest`
  - Call `conversation_service.fork_conversation()`
  - On `GitInitRequiredError`: return `409` with `{"error": "Git initialization required", "requiresGitInit": true, "sourcePath": "<path>"}`
  - On success: return `201` with `{"conversation": <ConversationResponse>}`
  - On streaming message: return `400`
  - On message not found: return `404`
- [x] 4.3 Modify `delete_conversation` in `conversation_service.py`: when deleting a conversation with `parent_conversation_id IS NOT NULL`, call `cleanup_fork_worktree(workspace.root_path, f"fork/{conversation_id}")`

## 5. Frontend: API & Store

- [x] 5.1 Add `forkConversation(conversationId, forkPointMessageId, confirmGitInit?)` function to `src/lib/api.ts` — `POST /api/conversations/{id}/fork`, returns `ConversationWithMeta`
- [x] 5.2 Handle `409 requiresGitInit` response in the API layer — return a structured error object `{ requiresGitInit: true, sourcePath: string }` that the caller can check
- [x] 5.3 Ensure `ConversationWithMeta` type in `src/shared/types.ts` includes `parentConversationId?: string` and `forkPointMessageId?: string`

## 6. Frontend: Fork Button & Interaction

- [x] 6.1 Add a fork button (GitBranch or Split icon from lucide-react) to `src/components/message-item.tsx` — visible on all completed agent messages (`!isUser && message.status !== 'streaming'`, not limited to latest). Place it in the hover action bar next to the regenerate button
- [x] 6.2 Wire the fork button click handler: call `forkConversation()`, on success `upsertConversation(newConv)` + `setActiveConversation(newConv.id)` + `fetchMessages(newConv.id)`
- [x] 6.3 Handle `requiresGitInit` response: show a confirmation dialog (`Dialog` from `components/ui/dialog`) with message "将在 {sourcePath} 初始化 Git 仓库以支持分支功能" and confirm/cancel buttons. On confirm, retry with `confirmGitInit: true`
- [x] 6.4 Add loading state on the fork button (spinner / disabled while API call is in flight)

## 7. Frontend: Fork Origin Banner

- [x] 7.1 Add a fork origin banner component (inline in `chat-panel.tsx` or as a small `ForkOriginBanner` component) — rendered above the message list when `conversation.parentConversationId` is set
- [x] 7.2 Banner text: "🔀 从 [{source_title}] 分支" — look up source title from `conversations` map by `parentConversationId`; fall back to "原对话" if not loaded
- [x] 7.3 Banner is dismissible with a close button (local state in the chat panel, reset on conversation switch)

## 8. Frontend: Sidebar Marker

- [x] 8.1 In `src/components/sidebar.tsx` `ConversationItem`, if `conversation.parentConversationId` is set, render a small `GitBranch` icon (size-3, muted-foreground) next to the conversation title
- [x] 8.2 Ensure all existing sidebar interactions (rename, pin, archive, delete) work the same for forked conversations

## 9. Tests

- [x] 9.1 Backend test: `fork_conversation` deep-copies messages and artifacts correctly (test_conversation_service.py or new test file)
- [x] 9.2 Backend test: hidden messages are excluded from fork copy
- [x] 9.3 Backend test: fork from streaming message returns error
- [x] 9.4 Backend test: fork preserves `agent_ids`, `mode`, `dispatch_mode`
- [x] 9.5 Backend test: `ensure_git_init` writes smart `.gitignore` when none exists
- [x] 9.6 Backend test: `ensure_git_init` appends `.agenthub-data/` when `.gitignore` already exists (no overwrite)
- [x] 9.7 Backend test: `create_fork_worktree` creates a worktree under the correct path
- [x] 9.8 Backend test: deleting a forked conversation calls `cleanup_fork_worktree`
- [x] 9.9 Backend test: `GitInitRequiredError` returned when source is non-git and `confirmGitInit=false`
- [x] 9.10 Backend test: fork API returns `409` with `requiresGitInit` when git init is needed
- [x] 9.11 Frontend test: fork button only appears on completed agent messages (if testable via message-item test)
