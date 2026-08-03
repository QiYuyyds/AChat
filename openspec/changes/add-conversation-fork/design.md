# Design: Add Conversation Fork

## Context

AChat 当前的消息操作（编辑重发、重新生成、撤回）都是破坏性的——删除原始消息再重新生成。用户无法保留原始分支同时探索新方向。市面上主流 Agent（ChatGPT、Claude、Cursor）都支持非破坏性分支。

AChat 有两个维度需要 fork：
1. **对话历史 fork**：消息深拷贝到新对话
2. **Workspace 文件 fork**：git worktree 隔离文件状态

项目已有 `worktree_service.py` 实现 `ensure_git_init()` + `create_worktree()` + `merge_worktree_back()`，但当前生命周期绑死 DAG 波（创建→运行→merge→清理）。Fork 需要的是持久化 worktree（创建→保留到对话删除）。

`build_history_for()` 当前查询逻辑是 `WHERE conversation_id = ? AND hidden = false ORDER BY created_at`——纯线性。Fork 的新对话拥有自己的消息副本，**不需要改这个查询**。

约束：
- 不引入新依赖
- 不修改 `build_history_for()` 的查询逻辑
- 复用 `worktree_service` 的 git 能力
- 前端改动最小化（新对话自然出现在 sidebar，message list 渲染不变）
- `Conversation` 与 `Workspace` 是 1:1 关系（`uselist=False`），fork 后新对话必须有自己的 Workspace 行

## Goals / Non-Goals

**Goals:**
- 用户可从任意 agent 消息（run 已结束）fork 出新对话
- 新对话继承截止 fork 点的全部消息历史（深拷贝）
- 新对话拥有独立的 workspace（git worktree 隔离），Agent 改文件不影响原对话
- 对非 git 的 local 目录，自动 `git init` 后再 worktree（统一路径）
- `build_history_for()` 零改动
- 原对话完全不受影响
- 首次在非 git 目录 fork 时，有确认提示

**Non-Goals:**
- 不实现原地树形分支（ChatGPT 式同对话内分支切换）——这是未来演进方向，当前选对话级 fork
- 不自动 merge fork 回原对话——用户可手动 `git merge fork/{conv_id}` 合并
- 不实现跨对话的 pinned / bookmark 联动——fork 后各对话独立 pin
- 不修改 `Message.parent_message_id` 语义——它仍然是「引用回复」UI 用途
- 不实现 fork 树状导航（展示 parent / children 列表）——MVP 只在 fork 出来的新对话顶部显示来源提示

## Decisions

### D1: Fork 方式 — 深拷贝消息 + git worktree 隔离 workspace

**选择**：创建新 Conversation 行，深拷贝截止 fork 点的消息到新对话；workspace 通过 `git worktree add` 隔离。

**备选**：
- 原地树形分支（ChatGPT 式）：需重写 `build_history_for` 为树遍历、前端需分支切换 UI、workspace 需 `git checkout` 切换——改动面巨大
- 引用式 fork（不拷贝消息，跨对话查）：需改 `build_history_for` 支持跨对话查询、父对话删除导致级联问题

**理由**：深拷贝 + worktree 方案下 `build_history_for` 零改动、前端渲染零改动、workspace 天然隔离。SQLite 本地场景下消息深拷贝成本可忽略（JSON 文本，几 KB~几十 KB/条）。两个 workspace 目录可同时打开、同时跑 Agent。

### D2: 非 git 目录处理 — 自动 `git init`

**选择**：fork 时若源目录不是 git 仓库，先调用增强版 `ensure_git_init()` 初始化，再创建 worktree。

**备选**：
- `shutil.copytree` 全量拷贝：大项目慢、不可合并、磁盘爆炸
- 共享同目录：两个对话 Agent 互相覆盖文件
- 弹窗让用户选策略：增加交互复杂度

**理由**：统一所有路径到 `git worktree`，代码逻辑最简。`ensure_git_init()` 已存在于 `worktree_service.py`，只需增强 `.gitignore` 模板。首次 fork 非 git 目录时有确认提示，后续不再提示。

### D3: 增强版 `.gitignore` — 智能模板 + 尊重已有

**选择**：`ensure_git_init()` 写 `.gitignore` 时分两种情况：
- 用户目录**没有** `.gitignore`：写入包含常见 ignore 模板的完整 `.gitignore`（node_modules / __pycache__ / dist / .env / .DS_Store / .agenthub-data/ 等）
- 用户目录**已有** `.gitignore`：只追加 `.agenthub-data/` 一行

**理由**：防止 `git add -A` 把 node_modules 等大目录全加进去导致慢和 `.git` 膨胀。尊重用户已有 `.gitignore` 不覆盖规则。

### D4: Fork 后 workspace mode 改为 sandbox

**选择**：fork 出的新对话 workspace `mode = "sandbox"`，`bound_path = null`，`root_path` 指向 `.agenthub-data/workspaces/users/{uid}/{conv_b_id}/`。

**理由**：新目录在 `.agenthub-data` 下，由 AChat 管理（100MB / 1000 文件配额生效）。不再是 local 模式因为不绑定用户原始路径。用户如果想合并回去，可手动 `cd 原项目 && git merge fork/{conv_id}`。

### D5: Fork 生命周期 — 持久化 worktree，不自动 merge-back

**选择**：fork 创建的 worktree 是**持久的**，不调用 `merge_worktree_back()` / `cleanup_worktree()`。仅在删除 fork 对话时清理 worktree + branch。

**理由**：DAG worktree 的生命周期是「波→merge→清理」，fork worktree 的生命周期是「对话存活期间」。两种生命周期完全不同，不复用 DAG 的 merge-back 流程。

### D6: Artifact 深拷贝策略

**选择**：fork 时深拷贝截止 fork 点的 Artifacts 到新对话。

- `web_app` / `document` / `image` / `ppt` / `diff`：content 存 DB，深拷贝行（新 ID、新 conversation_id）
- `code_file` / `project`：content 仅记 workspace 相对路径——worktree 有同样的文件，相对路径仍然有效，深拷贝行即可

**理由**：Artifact 通过 `conversation_id` FK CASCADE DELETE，不拷贝的话删除原对话时 Artifact 被级联删除，fork 对话里的 `artifact_ref` part 变成悬空引用。

### D7: Fork API — `POST /conversations/{id}/fork`

**选择**：新增 REST 端点 `POST /api/conversations/{conversation_id}/fork`，body 含 `forkPointMessageId`。

**响应**：返回新对话的完整 record（同 `create_conversation` 的响应格式），前端 `upsertConversation` + `setActiveConversation` 切过去。

### D8: 首次非 git fork 确认机制

**选择**：前端在调用 fork API 前，先检查源 workspace 是否需要 git init。如果需要，弹确认对话框：
> "将在 D:\projects\myapp 初始化 Git 仓库以支持分支功能。这会在该目录下创建 .git 文件夹和 .gitignore 文件。"

用户确认后传 `confirmGitInit: true` 到 API。同一目录后续 fork 不再提示（后端记住已 git init 的目录集合，或前端记住）。

## Risks / Trade-offs

- [大项目 `git add -A` 慢] → 智能先写 `.gitignore` 过滤 node_modules 等大目录；加超时日志告警但不阻断
- [用户不想被加 `.git`] → 首次确认提示；用户拒绝则 fork 失败并提示"请手动 git init 后再分支"或"使用 sandbox 模式对话"
- [消息深拷贝数据冗余] → SQLite 本地场景成本可忽略；未来如需可演进为引用式 fork
- [fork 链过深（fork of fork of fork）] → 不限制 fork 深度，但 sidebar 显示时加 `(分支)` 后缀避免混淆；`parent_conversation_id` 链可追溯
- [worktree 磁盘占用累积] → 删除 fork 对话时清理 worktree + branch；sandbox 模式 100MB 配额兜底
- [fork 后 Artifact 的新旧版本链断裂] → `parent_artifact_id` 只在对话内有效；fork 后的 Artifact 是全新行（新 ID），版本链从 1 重新开始。这是可接受的——fork 是"另起炉灶"
- [群聊 fork 后 agent_ids 拷贝] → 群聊 fork 时 `agent_ids` 一起拷贝到新对话，保持群聊配置不变
