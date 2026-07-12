## Why

当前 AChat 的记忆数据存在 PostgreSQL 表里（`long_term_memory`、`user_preferences`、`chat_history`、`memory_nodes`、`memory_edges`），用户完全看不到、改不了、删不了。这在本地单用户场景下是个体验问题和信任问题——用户不信任看不见的东西。

Claude Code 的记忆全部是 markdown 文件，用户可以直接打开、检查、编辑、删除，甚至 git 版本化。这种"全透明"设计是 Claude Code 记忆系统被用户信任的关键。

AChat 不需要照搬文件存储（PG 架构更适合多 Agent 平台），但需要提供同等的**透明度**——通过 API + UI 让用户能够：

- **查看**所有记忆条目（LTM 条目、Preference KV、Session Memory）
- **编辑**记忆内容、调整 importance、修改 category/tags
- **删除**错误或过时的记忆
- **按 Agent 分组**查看（依赖 `add-agent-scoped-memory`）

## What Changes

**A. 后端 API**

- `GET /api/memory/long-term` — 列出 LTM 条目，支持 `agent_id`、`category`、`tags` 过滤 + 分页
- `PUT /api/memory/long-term/{id}` — 编辑单条 LTM（content、importance、category、tags）
- `DELETE /api/memory/long-term/{id}` — 删除单条 LTM
- `GET /api/memory/preferences` — 列出所有 Preference KV
- `PUT /api/memory/preferences/{key}` — 编辑 Preference value
- `DELETE /api/memory/preferences/{key}` — 删除 Preference
- `GET /api/memory/session/{conversation_id}` — 查看会话的 Session Memory（依赖 `add-session-memory-layer`）

**B. 前端 UI**

- 设置面板新增"记忆管理" Tab
- 三个子面板：长期记忆 / 用户偏好 / 会话摘要
- 长期记忆面板：表格展示（content、category、importance、tags、agent_id、created_at），支持编辑/删除/筛选
- 用户偏对面板：KV 列表展示，支持编辑 value / 删除
- 会话摘要面板：按会话展示 Session Memory 文本，只读

**C. 安全约束**

- 编辑/删除操作需确认（前端二次确认弹窗）
- 删除 LTM 时同步清理 GraphMemory 中的节点和边
- 编辑 content 后重新计算 embedding
- API 不暴露 raw embedding（只返回元数据 + content）

## Capabilities

### New Capabilities

- `memory-management-api`: 记忆管理 REST API——CRUD 操作 LTM 条目和 Preference KV

### Modified Capabilities

- `frontend`: 设置面板新增"记忆管理" Tab

## Impact

- **后端代码**：新增 `backend/app/api/memory.py`（API 路由）；修改 `backend/app/memory/long_term.py`（update/delete 方法）、`backend/app/memory/preference.py`（delete 方法）、`backend/app/memory/graph_memory.py`（同步删除节点）
- **前端代码**：新增 `src/components/settings/memory-management/` 组件；修改设置页面布局
- **数据库**：无 schema 变更
- **API**：新增 7 个端点
- **风险**：用户删除记忆可能影响 Agent 行为——通过二次确认缓解；编辑 content 后 embedding 更新有延迟（异步）
