## ADDED Requirements

### Requirement: Agent message SHALL display fork button after run completion

When an agent message's associated run has ended (`status` not `streaming`), the `MessageItem` component MUST display a "🔀 分支" (fork) button in the hover action bar, alongside the existing reply / pin / regenerate buttons.

#### Scenario: Fork button appears on completed agent message

- **WHEN** an agent message has `runId` set and the run's `status` is `complete`, `failed`, or `aborted`
- **THEN** a fork button is rendered in the message action bar
- **AND** clicking it calls `POST /api/conversations/{id}/fork` with `forkPointMessageId` set to the message ID

#### Scenario: Fork button does not appear during streaming

- **WHEN** an agent message has `status = 'streaming'`
- **THEN** no fork button is rendered
- **AND** the existing regenerate button is also hidden (current behavior)

#### Scenario: Fork button does not appear on user messages

- **WHEN** a user message is rendered
- **THEN** no fork button is rendered on it (fork is only from agent responses)

### Requirement: Forked conversation SHALL display origin banner

When the active conversation has `parentConversationId` set, the chat panel MUST display a banner at the top of the message list indicating the fork origin.

#### Scenario: Fork origin banner content

- **WHEN** `conversation.parentConversationId` is not null
- **THEN** a banner is rendered above the first message showing: "🔀 从 [{source_title}] 的第 {N} 条消息分支"
- **AND** the source title is fetched from the conversations map (or falls back to "原对话" if not loaded)
- **AND** N is the 1-based position of the fork point in the source conversation
- **AND** the banner is dismissible (close button) but reappears on conversation switch

#### Scenario: Fork of fork shows nested origin

- **WHEN** a conversation was forked from another forked conversation
- **THEN** the banner still shows the immediate parent's title
- **AND** no recursive chain is displayed (MVP keeps it simple)

### Requirement: Fork action SHALL switch to new conversation

When the fork API call succeeds, the frontend MUST immediately switch to the new conversation.

#### Scenario: Successful fork switches conversation

- **WHEN** the user clicks the fork button and the API returns `201` with the new conversation record
- **THEN** the new conversation is upserted into the store (`upsertConversation`)
- **AND** `setActiveConversation(newConversationId)` is called
- **AND** the new conversation's messages are fetched (`fetchMessages`)
- **AND** the sidebar highlights the new conversation

#### Scenario: Git init confirmation needed

- **WHEN** the fork API returns `409` with `requiresGitInit: true`
- **THEN** a confirmation dialog is shown: "将在 {sourcePath} 初始化 Git 仓库以支持分支功能"
- **AND** if the user confirms, the fork is retried with `confirmGitInit: true`
- **AND** if the user cancels, no fork occurs

### Requirement: Sidebar SHALL mark forked conversations

Conversations that are forks (have `parentConversationId`) MAY display a visual indicator in the sidebar to distinguish them from original conversations.

#### Scenario: Forked conversation in sidebar

- **WHEN** a conversation with `parentConversationId` is rendered in the sidebar
- **THEN** it MAY show a small fork icon (🔀 or GitBranch) next to the title
- **AND** it MUST retain all existing sidebar functionality (rename, pin, archive, delete)
