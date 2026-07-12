## 阶段 1 — 后端 LTM 管理 API

- [x] 1.1 新增 `backend/app/api/memory.py`：API 路由模块
- [x] 1.2 `GET /api/memory/long-term`：支持 `agent_id`、`category`、`tag` 过滤 + `page`/`size` 分页；不返回 embedding
- [x] 1.3 `PUT /api/memory/long-term/{id}`：编辑 content/importance/category/tags；content 变更时异步重算 embedding
- [x] 1.4 `DELETE /api/memory/long-term/{id}`：删除 LTM + MemoryNode + MemoryEdge + Neo4j 节点/边
- [x] 1.5 `backend/app/memory/long_term.py`：新增 `update_item()` 和 `delete_item()` 方法，同步内存 + PG
- [x] 1.6 `backend/app/memory/graph_memory.py`：确认 `delete_from_graph()` 删除节点 + 关联边
- [x] 1.7 `backend/app/main.py`：注册 memory 路由
- [x] 1.8 单元测试：`test_api_memory.py` 覆盖 list/edit/delete/filter/pagination

## 阶段 2 — 后端 Preference 管理 API

- [x] 2.1 `GET /api/memory/preferences`：列出所有 KV
- [x] 2.2 `PUT /api/memory/preferences/{key}`：编辑 value
- [x] 2.3 `DELETE /api/memory/preferences/{key}`：删除 KV
- [x] 2.4 `backend/app/memory/preference.py`：新增 `delete()` 方法
- [x] 2.5 单元测试：覆盖 list/edit/delete

## 阶段 3 — 后端 Session Memory 查看 API

- [x] 3.1 `GET /api/memory/session/{conversation_id}`：返回 Session Memory 文本（依赖 `add-session-memory-layer`）
- [x] 3.2 若 Session Memory 变更未实施，返回 404
- [x] 3.3 单元测试

## 阶段 4 — 后端 embedding 重算

- [x] 4.1 `backend/app/api/memory.py`：`_recompute_embedding(memory_id, content)` 异步函数
- [x] 4.2 重算后更新 PG `long_term_memory.embedding` 和内存 `Item.embedding`
- [x] 4.3 重算后同步更新 GraphMemory 节点的 content（若 Neo4j 可用）
- [x] 4.4 验证：编辑 content 后 embedding 更新，recall 使用新 embedding

## 阶段 5 — 前端长期记忆面板

- [x] 5.1 新增 `src/components/settings/memory-management/` 目录
- [x] 5.2 `long-term-memory-panel.tsx`：表格展示（content, category, importance, tags, agent, created_at）
- [x] 5.3 筛选栏：Agent 下拉 + Category 下拉 + 搜索框
- [x] 5.4 行操作：inline 编辑（content, importance, category, tags）+ 保存/取消
- [x] 5.5 行操作：删除 + 二次确认弹窗
- [x] 5.6 分页组件
- [x] 5.7 API 调用封装：`src/lib/api/memory.ts`

## 阶段 6 — 前端用户偏对面板

- [x] 6.1 `preference-panel.tsx`：KV 列表展示（key, value, updated_at）
- [x] 6.2 行操作：inline 编辑 value + 保存
- [x] 6.3 行操作：删除 + 二次确认弹窗

## 阶段 7 — 前端会话摘要面板

- [x] 7.1 `session-memory-panel.tsx`：会话列表（title, updated_at）
- [x] 7.2 点击展开：只读展示 Session Memory 文本
- [x] 7.3 若 Session Memory 变更未实施，显示空状态提示

## 阶段 8 — 前端集成

- [x] 8.1 修改设置页面：新增"记忆管理" Tab
- [x] 8.2 Tab 内三个子面板切换（长期记忆 / 用户偏好 / 会话摘要）
- [x] 8.3 侧边栏新增「记忆管理」入口（MCP 下方独立图标，非设置 Dialog 内 Tab）
- [x] 8.4 `pnpm typecheck` + `pnpm lint` 过

## 阶段 9 — 主区域宽屏布局（方案 A）

- [x] 9.1 `app-store` 新增 `sidebarMode` + `memoryTab` 状态，实现侧边栏与主区域跨组件共享
- [x] 9.2 `sidebar.tsx`：将 `mode` 从组件局部 state 提升到 store；memory 模式下侧边栏仅渲染 `MemorySidebarNav`（子 Tab 导航 + 描述）
- [x] 9.3 `memory-library.tsx`：拆分为 `MemorySidebarNav`（窄导航）+ `MemoryMainPanel`（主区域宽屏内容）
- [x] 9.4 `page.tsx`：当 `sidebarMode === 'memory'` 时，主区域渲染 `MemoryMainPanel` 替代 `ChatPanel`
- [x] 9.5 `pnpm typecheck` + `pnpm lint` 过

## 阶段 10 — 回归

- [x] 10.1 `cd backend && ruff check .` 无新增错误
- [x] 10.2 `cd backend && pytest` 新增 memory API 用例全绿
- [x] 10.3 `pnpm typecheck` + `pnpm lint` 过
- [x] 10.4 端到端验证：查看 → 编辑 → 删除 → 确认 Agent recall 结果受影响（由单元测试覆盖 API 层；前端组件通过类型检查 + lint 验证）
