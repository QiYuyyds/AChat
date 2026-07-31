## 1. Worktree 接入 dispatch 流程

- [x] 1.1 在 `dag_executor._execute_node` 中，调用 `spawn_subagent_loop` 前调用 `create_worktree()` 创建隔离工作目录，将 worktree 路径传给 `spawn_subagent_loop(workspace_path=wt.path)`
- [x] 1.2 在 `dag_executor._execute_node` 中，子任务完成后调用 `merge_worktree_back(wt)`，然后无论 merge 成功与否都调用 `cleanup_worktree(wt)`
- [x] 1.3 在 `task_dispatch._handler` 中，调用 `spawn_subagent_loop` 前调用 `create_worktree()`，完成后调用 `merge_worktree_back()` + `cleanup_worktree()`
- [x] 1.4 处理 `create_worktree()` 返回 `None` 的降级路径：跳过 merge-back，直接用共享 workspace 模式（当前行为）
- [x] 1.5 在 `dag_executor._execute_node` 和 `task_dispatch._handler` 中获取 `agent_name`（从 Agent 表查询）和 `task_id` 传给 `create_worktree()`

## 2. MergeResult 扩展

- [x] 2.1 在 `MergeResult` dataclass 中新增 `resolution_strategy: str = "auto"` 字段（取值：`"auto"` / `"llm"` / `"manual"` / `"abandoned"`）
- [x] 2.2 在 `MergeResult` dataclass 中新增 `resolved_files: list[str]` 字段（记录被 LLM 或人工解决的文件列表）
- [x] 2.3 更新 `merge_worktree_back()` 在 Layer 1 成功路径返回 `resolution_strategy="auto"`

## 3. Layer 2: LLM 辅助冲突解决

- [x] 3.1 在 `worktree_service.py` 中新增 `_extract_conflict_content(workspace_path, file_path) -> str` 函数：读取冲突文件，提取 `<<<<<<<` / `=======` / `>>>>>>>` 标记内容
- [x] 3.2 在 `worktree_service.py` 中新增 `_build_llm_merge_prompt(file_path, conflict_content) -> str` 函数：构造 LLM prompt（只含文件路径和冲突内容，不含 task description）
- [x] 3.3 在 `worktree_service.py` 中新增 `_call_llm_merge(prompt) -> str` 函数：调用 LLM（复用 `eval_judge.py` 的 httpx + key 优先级模式），返回合并后的文件内容
- [x] 3.4 在 `worktree_service.py` 中新增 `_validate_syntax(file_path, content) -> bool` 函数：按文件扩展名做语法检查（`.py` 用 `compile()`，`.json` 用 `json.loads()`，`.ts`/`.tsx`/`.js` 用括号配对 regex，`.md` 等跳过）
- [x] 3.5 在 `worktree_service.py` 中新增 `_llm_resolve_conflicts(workspace_path, conflict_files) -> tuple[bool, list[str]]` 函数：遍历冲突文件，逐个调用 LLM 合并 + 语法检查，返回（是否全部成功，已解决文件列表）
- [x] 3.6 在 `merge_worktree_back()` 中，当 `git merge` 返回非零时，调用 `_llm_resolve_conflicts()`；成功则 `git add` + `git commit` 完成合并，返回 `resolution_strategy="llm"`

## 4. Layer 3: 人工审批 + 数据快照

- [x] 4.1 新建 `backend/app/services/pending_merge_conflicts.py`，复用 `pending_writes.py` 模式：`register()` / `wait_for_decision()` / `resolve()` 方法，使用 `asyncio.Event` 阻塞等待
- [x] 4.2 在 `worktree_service.py` 中新增 `_save_conflict_snapshots(wt, conflict_files) -> list[str]` 函数：用 `git show :1:<file>` / `:2:<file>` / `:3:<file>` 提取 base/ours/theirs 三份内容，创建 Artifact（type=`diff`）
- [x] 4.3 在 `merge_worktree_back()` 中，当 Layer 2 失败时：调用 `_save_conflict_snapshots()` 保存快照，调用 `pending_merge_conflicts.register()` 注册审批，`await wait_for_decision()` 阻塞等待
- [x] 4.4 用户决策处理：「保留我方」→ `git checkout --ours`；「保留对方」→ `git checkout --theirs`；「手动编辑」→ 写入用户提交的内容；「放弃」→ `git merge --abort`
- [x] 4.5 所有冲突文件解决后 `git add` + `git commit`，返回 `resolution_strategy="manual"`；放弃时返回 `resolution_strategy="abandoned"`

## 5. SSE 事件 + API 端点

- [x] 5.1 在 `WorktreeEvent` 中新增 `conflict_files: list[str] | None` 和 `resolution_status: str | None` 字段（camelCase 别名）
- [x] 5.2 新增 `MergeConflictPendingEvent` SSE 事件类型（含 conversation_id / task_id / conflict_files / pending_id / workspace_path）
- [x] 5.3 新增 `MergeConflictResolvedEvent` SSE 事件类型（含 pending_id / resolution_strategy / resolved_files）
- [x] 5.4 在 `backend/app/api/pending.py` 中新增 `POST /api/pending/merge-conflicts/{pending_id}/resolve` 端点，接收 `{ action: "ours" | "theirs" | "edit" | "abandon", file_contents?: dict }`
- [x] 5.5 在 `backend/app/api/pending.py` 中新增 `GET /api/pending/merge-conflicts` 端点，列出当前待解决的合并冲突
- [x] 5.6 更新 `_publish_worktree_event()` 在冲突时携带 `conflict_files` 和 `resolution_status`

## 6. 前端冲突解决面板

- [x] 6.1 新建 `src/components/merge-conflict-panel.tsx`，复用 `PendingWritesPanel` + `DiffBlock` 模式
- [x] 6.2 面板展示冲突文件列表，每个文件可展开查看 base/ours/theirs 三方对比（复用 `DiffBlock` 组件）
- [x] 6.3 每个冲突文件提供四个操作按钮：「保留我方」/「保留对方」/「手动编辑」/「放弃此任务」
- [x] 6.4 「手动编辑」打开 `ArtifactCodeEditor`（复用已有组件），用户编辑后提交
- [x] 6.5 在 `src/shared/types.ts` 中同步 `WorktreeEvent` / `MergeConflictPendingEvent` / `MergeConflictResolvedEvent` 类型定义
- [x] 6.6 在 `src/stores/app-store.ts` 的 event reducer 中处理 `merge_conflict.pending` 和 `merge_conflict.resolved` 事件

## 7. 测试

- [x] 7.1 单元测试：`_extract_conflict_content()` 正确提取冲突标记
- [x] 7.2 单元测试：`_validate_syntax()` 对各文件类型的正确/错误用例
- [x] 7.3 单元测试：`_llm_resolve_conflicts()` mock LLM 返回，验证成功/失败路径
- [x] 7.4 集成测试：`merge_worktree_back()` Layer 1 成功路径（无冲突）
- [x] 7.5 集成测试：`merge_worktree_back()` Layer 2 LLM 成功路径（mock LLM 返回有效合并 + 语法通过）
- [x] 7.6 集成测试：`merge_worktree_back()` Layer 3 人工审批路径（mock pending store，模拟用户决策）
- [x] 7.7 集成测试：`dag_executor._execute_node` worktree 创建/merge-back/cleanup 全流程
- [x] 7.8 集成测试：`task_dispatch._handler` worktree 创建/merge-back/cleanup 全流程
- [x] 7.9 集成测试：`create_worktree()` 返回 None 时降级为共享 workspace 模式
