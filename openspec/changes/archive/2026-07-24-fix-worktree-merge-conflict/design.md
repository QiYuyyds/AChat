# Design: Fix Worktree Merge Conflict

## Context

`worktree_service.py` 实现了完整的 worktree 生命周期管理（create → merge-back → cleanup），但存在两个问题：

1. **未接入 dispatch 流程**：`dag_executor._execute_node` 和 `task_dispatch._handler` 都没有调用 `create_worktree`，并行子 Agent 共享同一个 workspace 目录，文件写入互相覆盖。
2. **merge 冲突直接放弃**：`merge_worktree_back()` 在 `git merge` 返回非零时直接 `merge --abort`，冲突文件列表通过 `MergeResult.conflict_files` 返回但无人消费，子 Agent 的工作成果静默丢失。

当前 `merge_worktree_back` 的冲突处理代码（第 293-300 行）：

```python
if rc != 0:
    conflict_files = _parse_conflict_files(wt.main_workspace_path)
    await _run_git(wt.main_workspace_path, "merge", "--abort")  # ← 直接放弃
    result = MergeResult(success=False, conflict_files=conflict_files, ...)
```

约束：
- per-workspace mutex lock（`_WorktreeLockManager`）保证同一 workspace 的 git 操作串行化
- `spawn_subagent_loop` 已有 `workspace_path: str | None` 参数和 `RunArgs.override_workspace_path` 字段
- 项目已有 `pending_writes.py` / `pending_bash_commands.py` 审批模式可复用
- 项目已有 `DiffBlock` / `PendingWritesPanel` 前端组件可复用

## Goals / Non-Goals

**Goals**:
- 将 worktree 隔离接入 `dag_executor` 和 `task_dispatch`，使并行子 Agent 获得独立工作目录
- merge 冲突时不再静默丢失数据，通过三层递进策略解决冲突
- Layer 2 LLM 合并仅依赖冲突文件内容（不依赖 task description / agent system prompt）
- Layer 3 人工审批阻塞等待用户决策，不超时自动降级
- 冲突数据保底：三份快照（base / ours / theirs）保存为 Artifact

**Non-Goals**:
- 不实现文件级分区预防（`exclusive_files` 声明）——留作后续优化
- 不实现超时自动降级——用户明确要求「宁阻塞等人」
- 不修改 `spawn_subagent_loop` 的函数签名——worktree 路径通过已有的 `override_workspace_path` 传递
- 不实现 non-git 模式的冲突解决——non-git fallback 使用 `copytree` 覆盖，无冲突概念
- 不引入新的外部依赖——LLM 合并复用现有 httpx / openai SDK

## Decisions

### D1: Worktree 接入点 — `_execute_node` 和 `_handler` 各自管理

**选择**：在 `dag_executor._execute_node` 和 `task_dispatch._handler` 中各自调用 `create_worktree` / `merge_worktree_back` / `cleanup_worktree`，而非在 `spawn_subagent_loop` 中统一管理。

**理由**：
- `spawn_subagent_loop` 是通用的子 Agent 派发入口，不应耦合 worktree 逻辑
- `dag_executor` 和 `task_dispatch` 对 worktree 的需求不同：DAG executor 需要在波调度结束后统一 merge-back，task_dispatch 需要在单个任务完成后立即 merge-back
- worktree 创建需要 `agent_name` 和 `task_id`，这些信息在调用方手中

**替代方案**：在 `spawn_subagent_loop` 中统一管理 worktree 生命周期。否决原因：`spawn_subagent_loop` 无法区分 DAG 波调度和单任务派发的不同 merge-back 时机。

### D2: 三层递进冲突解决 — 自动 → LLM → 人工

**选择**：Layer 1 标准 `git merge` → Layer 2 LLM 合并 → Layer 3 人工审批。

**Layer 1**（已有）：`git merge --no-edit <branch>`，成功则结束。

**Layer 2**（新增）：提取冲突文件的 `<<<<<<<` / `=======` / `>>>>>>>` 标记内容，构造 prompt：

```
你是一个代码合并专家。以下文件存在 git 合并冲突，请综合两边的修改，
生成一个语义正确的合并版本。只输出合并后的完整文件内容，不要解释。

文件路径: {file_path}

冲突内容:
{conflict_content}
```

LLM 返回后写入文件，做语法检查：
- `.ts` / `.tsx` / `.js`：检查括号匹配（简单 regex `{}` `()` `[]` 配对）
- `.py`：`python -c "compile(open(file).read(), file, 'exec')"` 子进程
- `.json`：`json.loads()`
- `.md` / 其他：跳过语法检查，直接通过

语法检查通过 → `git add` → 继续合并下一个冲突文件。
语法检查失败 → 降级到 Layer 3。

**Layer 3**（新增）：保持冲突状态（不 abort），发布 `MergeConflictPendingEvent` SSE 事件，推到前端冲突解决面板。同时保存三份快照到 Artifact：

```
artifact: {
  type: "diff",
  title: "合并冲突: {filename}",
  content: {
    base:  <共同祖先版本>,
    ours:  <主分支版本>,
    theirs: <worktree 分支版本>,
    conflict_markers: <原始冲突标记内容>,
  }
}
```

用户选择：
- 「保留我方」→ `git checkout --ours <file>` → `git add`
- 「保留对方」→ `git checkout --theirs <file>` → `git add`
- 「手动编辑」→ 前端打开代码编辑器，用户编辑后提交内容 → 写入文件 → `git add`
- 「放弃此任务」→ `git merge --abort` → `MergeResult(success=False)`

所有冲突文件解决后 → `git commit` → `MergeResult(success=True, resolution_strategy="manual")`。

**替代方案**：
- 纯 LLM 合并（无人工 fallback）：否决，LLM 可能产生错误合并，需要人类保底
- 纯人工审批（无 LLM）：否决，少量冲突时 LLM 可自动解决，减少用户负担
- 超时自动降级：否决，用户明确要求「宁阻塞等人」

### D3: Worktree 创建时机 — 每个并行任务一个 worktree

**选择**：`dag_executor` 的每个 wave 中的每个 ready task 各创建一个 worktree；`task_dispatch` 每次调用创建一个 worktree。

**理由**：
- worktree 的 lifetime == task 的 lifetime，简单直观
- 同一 wave 内的并行任务各自独立，互不干扰

**替代方案**：每个 wave 共享一个 worktree。否决原因：同一 wave 内的并行任务可能修改同一文件，共享 worktree 无法隔离。

### D4: Merge-back 时机 — 任务完成后立即 merge

**选择**：每个任务完成后立即 merge-back（而非等整个 wave 完成后统一 merge）。

**理由**：
- per-workspace mutex lock 保证 merge 串行化，不会并发冲突
- 任务完成后立即 merge 可以尽早发现冲突，给用户更多时间处理
- 如果等 wave 全部完成再统一 merge，失败的 merge 会阻塞整个 wave 的后续任务

### D5: LLM 合并的 API 复用

**选择**：复用 `eval_judge.py` 中的 LLM 调用模式（httpx 直接调 OpenAI 兼容 API），按优先级选择 key：`llm_api_key` > `openai_api_key` > `deepseek_api_key`。

**理由**：不引入新依赖，复用已有的 key 解析逻辑。

### D6: 冲突审批 store — 复用 pending_writes 模式

**选择**：新建 `pending_merge_conflicts.py`，结构复用 `pending_writes.py` 的 `register` / `wait_for_decision` / `resolve` 模式。

**理由**：
- 审批流程与 fs_write 审批几乎相同（等待用户决策 → 执行操作）
- 复用模式降低认知成本

## Risks / Trade-offs

- **[Risk] LLM 合并产生语义错误但通过语法检查** → 语法检查是必要非充分条件；Layer 3 人工审批作为最终保底；冲突快照保存为 Artifact 可事后追溯
- **[Risk] 并行任务大量修改同一文件导致频繁冲突** → Layer 2 LLM 可自动解决大部分；后续可引入文件级分区（exclusive_files）预防
- **[Risk] 人工审批阻塞导致 DAG 后续 wave 无法执行** → 这是预期行为（用户选择「宁阻塞等人」）；冲突解决后 DAG 继续执行；用户可选择「放弃此任务」快速跳过
- **[Risk] Worktree 创建失败（磁盘空间不足 / 路径过长）** → `create_worktree` 返回 None 时降级为无 worktree 模式（共享 workspace），与当前行为一致
- **[Trade-off] 每个任务一个 worktree 增加磁盘开销** → 任务完成后立即 `cleanup_worktree` 清理；sandbox 模式有 100MB 配额限制
- **[Trade-off] LLM 合并消耗 token** → 仅在冲突时触发，非每次 merge 都调用；prompt 只含冲突文件内容，不含完整 task 上下文

## Migration Plan

1. 先实现 worktree 接入（create/merge-back/cleanup 调用），不含冲突解决——此时行为与当前一致（冲突时 abort）
2. 再实现 Layer 2 LLM 合并——冲突时自动尝试 LLM 解决
3. 最后实现 Layer 3 人工审批——LLM 失败时阻塞等待用户
4. 每步都可独立测试，不破坏现有功能
5. 无 DB schema 变更，无数据迁移

## Open Questions

无。用户已明确所有关键决策：
- worktree 接入与冲突解决一起做 ✓
- LLM prompt 只含冲突文件内容 ✓
- 宁阻塞等人，不超时降级 ✓
