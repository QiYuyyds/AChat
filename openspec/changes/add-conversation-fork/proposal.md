# Add Conversation Fork

## Why

当前对话的「编辑重发」「重新生成」「撤回」操作都是**破坏性**的——原始消息被删除，无法回看之前的分支。市面上主流 Agent（ChatGPT、Claude、Cursor）都支持非破坏性分支：从某条消息开始分叉出一个新方向，原对话保留不变。AChat 已有 `worktree_service` 做 git worktree 隔离，可以复用来实现对话级 fork + workspace 级 fork 的双重隔离。

## What Changes

- 新增 `fork_conversation()` API：从指定消息点深拷贝对话历史到新对话，同时通过 git worktree 隔离 workspace 文件状态
- **BREAKING**：`Conversation` 模型新增 `parent_conversation_id` + `fork_point_message_id` 两个可空列
- 对非 git 的 local 目录，fork 时自动 `git init`（复用 `ensure_git_init`，增强 `.gitignore` 智能模板）
- 前端在 agent 消息（run 已结束）上新增「🔀 分支」按钮，点击后创建 fork 并切换到新对话
- 新对话顶部显示 fork 来源提示条
- `build_history_for()` **零改动**——新对话拥有自己的消息副本，查询逻辑不变
- Workspace 隔离统一走 `git worktree add`，不再区分 copytree / 共享目录等降级路径
- fork 后的新对话 workspace `mode` 改为 `"sandbox"`（AChat 管理目录），`bound_path` 置空
- 删除 fork 对话时清理对应 git worktree + branch

## Capabilities

### New Capabilities

- `conversation-fork`: 对话级 fork 能力——从指定消息点创建新对话（深拷贝历史 + workspace 隔离），非破坏性分支

### Modified Capabilities

- `persistence`: Conversation 表新增 `parent_conversation_id` / `fork_point_message_id` 列；Workspace 创建流程新增 fork 路径
- `core-domain`: Conversation 实体新增 fork 关系字段（parent / fork point）
- `frontend`: MessageItem 新增 fork 按钮；新对话顶部 fork 来源提示条；Sidebar 对话项 fork 标记

## Impact

- **后端**：
  - `backend/app/db/models.py`：Conversation 新增 2 列
  - `backend/app/db/engine.py`：启动时 `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS`
  - `backend/app/services/conversation_service.py`：新增 `fork_conversation()` + 消息/artifact 深拷贝逻辑
  - `backend/app/services/worktree_service.py`：增强 `ensure_git_init()` 的 `.gitignore` 模板；新增 `create_fork_worktree()` 持久化 worktree（不复用 DAG 的 merge-back 生命周期）
  - `backend/app/api/conversations.py`：新增 `POST /conversations/{id}/fork` 端点
  - `backend/app/schemas/requests.py`：新增 `ForkConversationRequest`
- **前端**：
  - `src/components/message-item.tsx`：agent 消息上新增 fork 按钮
  - `src/components/chat-panel.tsx`：fork 来源提示条
  - `src/lib/api.ts`：新增 `forkConversation()` API 调用
  - `src/stores/app-store.ts`：可能需要 fork 来源信息展示 state
- **依赖**：无新第三方依赖（复用现有 git CLI + worktree_service）
