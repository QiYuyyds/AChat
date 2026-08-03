# Fix Worktree Merge Conflict

## Why

Worktree 隔离的 `merge_worktree_back()` 在遇到 git merge 冲突时直接 `merge --abort` 放弃合并，导致并行子 Agent 的工作成果静默丢失。同时，`worktree_service.py` 的 `create_worktree` / `merge_worktree_back` / `cleanup_worktree` 虽已实现完整生命周期，但 `dag_executor._execute_node` 和 `task_dispatch._handler` 都没有调用它们——并行子 Agent 实际共享同一个 workspace，连冲突都不会产生。本变更一次性解决两个问题：将 Worktree 隔离接入 dispatch 流程，并为 merge 冲突设计三层递进解决策略（自动三方合并 → LLM 辅助合并 → 人工审批）。

## What Changes

- 将 `create_worktree` / `merge_worktree_back` / `cleanup_worktree` 接入 `dag_executor._execute_node` 和 `task_dispatch._handler`，使并行子 Agent 获得独立的 git worktree 工作目录
- `merge_worktree_back()` 遇到冲突时不再直接 `merge --abort`，改为保持冲突状态并启动三层递进解决流程
- 新增 Layer 1：标准 `git merge --no-edit` 三方合并（已有，不变）
- 新增 Layer 2：LLM 辅助冲突解决——提取冲突文件中的 `<<<<<<<` / `=======` / `>>>>>>>` 标记内容，构造 prompt 让 LLM 生成合并版本，写入后做语法检查（通过则 `git add` + commit，不通过则降级到 Layer 3）
- 新增 Layer 3：人工审批——保持冲突状态，发布 SSE 事件推到前端冲突解决面板，用户可选择「保留我方」/「保留对方」/「手动编辑」/「放弃此任务」，同时保存三份快照（base/ours/theirs）到 Artifact 确保数据不丢
- **BREAKING**：`MergeResult` dataclass 新增 `resolution_strategy` 字段和 `resolved_files` 字段
- 新增 `pending_merge_conflicts.py` 内存 store（复用 `pending_writes.py` 审批模式）
- 新增前端 `MergeConflictPanel` 组件（复用 `PendingWritesPanel` + `DiffBlock` 模式）
- `WorktreeEvent` SSE 事件新增 `conflict_files` 和 `resolution_status` 字段
- `spawn_subagent_loop` 签名不变，worktree 路径通过 `RunArgs.override_workspace_path` 传递（已有字段）

## Capabilities

### New Capabilities

- `worktree-conflict-resolution`: Worktree merge-back 冲突的三层递进解决策略（自动合并 → LLM 辅助 → 人工审批），包含冲突检测、LLM 合并 prompt 构造、语法验证、人工审批流程、数据快照保底

### Modified Capabilities

- `orchestrator`: DAG executor 的 `_execute_node` 在执行子任务前创建 worktree、执行后 merge-back 并处理冲突，task_dispatch 的 `_handler` 同理

## Impact

- **后端**：
  - `backend/app/services/worktree_service.py`：`merge_worktree_back` 重构为三层递进；新增 `_llm_resolve_conflicts()` / `_save_conflict_snapshots()` 函数
  - `backend/app/services/dag_executor.py`：`_execute_node` 新增 worktree 创建/merge-back/cleanup 调用
  - `backend/app/tools/task_dispatch.py`：`_handler` 新增 worktree 创建/merge-back/cleanup 调用
  - `backend/app/services/pending_merge_conflicts.py`：新建，冲突审批内存 store
  - `backend/app/schemas/events.py`：`WorktreeEvent` 新增字段
  - `backend/app/api/pending.py`：新增冲突审批端点
- **前端**：
  - `src/components/merge-conflict-panel.tsx`：新建，冲突解决面板
  - `src/shared/types.ts`：`WorktreeEvent` 类型同步
- **无新依赖**：LLM 合并复用现有 `openai` SDK / httpx 调用；语法检查复用项目内已有的 ts/py 解析能力或简单 regex 校验
