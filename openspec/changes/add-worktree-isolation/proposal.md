# Proposal: Worktree Isolation for Parallel Dispatch Tasks

## Why

Orchestrator 同波次并行子任务共享同一个 workspace 目录，当前靠 `detect_wave_conflicts` 做事后检测（hash 对比）——只能上报冲突，不能预防。两个 agent 同时写 `package.json` 就会后写覆盖先写，丢改动。这限制了 Loop Engineering 的并行能力：无法安全地在同一仓库上同时跑多个独立任务。

引入 git worktree 后，同波次的每个子任务获得独立的工作目录和分支，物理隔离、互不干扰；而同一子任务的 harness loop 续跑（attempt 2/3/4）仍在同一个 worktree 内串行执行，保持"不要从头再来"的语义。DAG 的 wave 边界天然匹配 worktree 生命周期——wave 开始创建 worktree，wave 结束 merge 回主 workspace 再清理。

## What Changes

- **新增 `worktree_service.py`**：封装 worktree 创建、merge-back、清理的完整生命周期管理。支持 git 仓库（真 worktree）和非 git 目录（降级为目录拷贝）两种模式。
- **`_execute_dag` 集成 worktree**：每个 wave 开始前为 `ready` 中的每个 task 创建 worktree；wave 结束后，完成的任务 merge 回主 workspace，清理 worktree；失败的任务不 merge（但保留 worktree 供调试，由 GC 清理）。
- **`_run_child_task` 传入 worktree path**：子任务的 `ToolContext.workspace_path` 指向 worktree 路径而非主 workspace 路径；harness loop 的多次 attempt 在同一个 worktree 内串行续跑，共享文件状态。
- **`detect_wave_conflicts` 降级为 advisory** **BREAKING**：worktree 模式下同波次并行任务物理隔离，不再产生文件冲突。`detect_wave_conflicts` 保留但仅在非 worktree 模式（降级路径）下作为 advisory 检测，不阻断流程。
- **sandbox 模式自动 git init**：sandbox workspace 在创建时自动 `git init`，使其支持真 worktree；local 模式天然使用用户的 git 仓库。
- **新增 `WorktreeEvent` SSE 事件**：worktree 创建、merge、清理时发布事件，前端可显示分支名和 merge 状态。
- **worktree 目录管理**：worktree 创建在 `.agenthub-data/worktrees/<conv_id>/<task_id>/` 下，与 workspace 目录分离；启动时自动清理残留的孤儿 worktree。

## Capabilities

### New Capabilities

- `worktree-isolation`: Dispatch 子任务的 worktree 隔离生命周期管理——创建、merge-back、清理、降级策略

### Modified Capabilities

- `orchestrator`: `_execute_dag` 在 wave 级别创建/merge/cleanup worktree；`_run_child_task` 传入 worktree path 作为 effective cwd；`detect_wave_conflicts` 降级为 advisory
- `platform-security`: worktree 目录路径安全检查；sandbox 模式 git init 的安全约束；worktree 清理的子进程安全

## Impact

- **新增文件**：`backend/app/services/worktree_service.py`
- **修改文件**：
  - `backend/app/services/orchestrator.py`（`_execute_dag` + `_run_child_task` worktree 集成）
  - `backend/app/utils/workspace_utils.py`（worktree path 解析支持）
  - `backend/app/services/conversation_service.py`（sandbox 模式 git init）
  - `backend/app/schemas/events.py` + `src/shared/types.ts`（新增 `WorktreeEvent`）
  - `backend/app/db/models.py`（可选：`worktree_instances` 表用于崩溃恢复）
- **Spec 文档**：`specs/06-orchestrator-flow.md` 新增 "Worktree 隔离" 章节，更新 "代码冲突检测" 章节
- **前端**：`dispatch-plan-card.tsx` 显示 worktree 分支名和 merge 状态
- **依赖**：无新增外部依赖（使用系统 `git` 命令行，已在 bash 工具中依赖）
- **平台兼容**：Windows 上 git worktree 需要 git ≥ 2.15；`待融合项目/multica-main` 已有完整的 Go 实现（`repocache/cache.go`）可参考分支命名、并发锁、清理逻辑
