## Context

当前 Orchestrator 的 `_execute_dag` 将同一波次的并行子任务扔进 `asyncio.gather`，所有子任务的 `fs_write` / `bash` 操作共享同一个 workspace 目录。`detect_wave_conflicts` 在 wave 结束后做 hash 对比检测冲突——只能上报，不能预防，且 bash 写文件不经过 `fs_write` 是已知盲区。

这限制了 Loop Engineering 的并行能力。参考 multica-main 的 `repocache/cache.go` 实现（`agent/{sanitized-name}/{short-task-id}` 分支命名、per-repo mutex lock、stale branch GC），可以在 AChat 中引入 git worktree 实现物理隔离。

关键约束：同一子任务的 harness loop 续跑（attempt 2/3/4）必须在同一个 worktree 内串行执行，保持"不要从头再来"语义。DAG 的 wave 边界天然匹配 worktree 生命周期。

## Goals / Non-Goals

**Goals:**

- 同波次并行子任务各自在独立 worktree 中执行，物理隔离文件系统
- harness loop 多次 attempt 在同一个 worktree 内串行续跑，共享文件状态
- wave 结束后完成的任务 merge 回主 workspace，下游任务基于已合并状态创建新 worktree
- 支持 git 仓库（真 worktree）和非 git 目录（降级为目录拷贝）
- sandbox 模式自动 git init 使其支持真 worktree

**Non-Goals:**

- 不实现跨会话持久 worktree（worktree 生命周期限定在单个 dispatch round 内）
- 不实现自动开 PR（merge 回主 workspace 即完成，PR 是后续 Loop Engineering 的能力）
- 不改 CLI adapter 的执行路径（Claude CLI / Codex CLI 的工具由 CLI 自管，worktree path 通过 `--cwd` 或 `extra_env` 传入）
- 不改 StreamEvent 核心协议（新增 `worktree` 事件类型是追加，不破坏现有事件）
- 不实现 worktree 级别的配额管理（sandbox 模式的 100MB/1000 文件配额仍作用于整个 workspace 目录树）

## Decisions

### Decision 1: Worktree 生命周期绑定到 DAG Wave，不是单个 attempt

**选择**: worktree 在 wave 开始时创建，wave 结束时 merge + 清理。同一 task 的多次 attempt 共享同一个 worktree。

**理由**:
- DAG wave 是天然的同步点——同一 wave 内的任务无依赖关系，可安全并行
- wave 之间是串行的，merge 在下一个 wave 创建 worktree 之前完成，下游天然看到上游产出
- harness loop 的设计意图是"在同一个文件系统状态上续跑"，worktree per-attempt 会破坏这个语义

**备选方案**: per-attempt worktree → 被否决，因为 attempt 2 需要看到 attempt 1 写的文件

### Decision 2: 分支命名 `agent/{sanitized-agent-name}/{short-task-id}`

**选择**: 参考 multica-main 的命名方案，`agent/{sanitized-agent-name}/{short-task-id}`。

**理由**:
- `agent/` 前缀统一命名空间，便于 GC 识别和清理（`git for-each-ref refs/heads/agent/`）
- agent name 便于人工调试时识别 worktree 归属
- task id 保证唯一性

**备选方案**: `achat/{conversation_id}/{task_id}` → 被否决，conversation_id 太长且不便于人工识别

### Decision 3: Merge 策略 — git merge 优先，目录拷贝降级

**选择**:
- **git 仓库**（local 模式 / sandbox 模式 git init 后）: worktree 内 `git add -A && git commit`，然后主 workspace `git merge --no-edit agent/x/t1`
- **非 git 目录**（降级）: 用 `shutil.copytree` 把 worktree 目录的文件复制回主 workspace（覆盖策略）
- merge 冲突时：记录冲突文件列表，标记 task 为 `merge_conflict` 状态，注入聚合 prompt 让 Orchestrator 告知用户

**理由**:
- local 模式用户已有 git 仓库，merge 是自然操作
- sandbox 模式自动 git init 后也能用真 worktree
- 非 git 目录降级保证不会因为环境缺失而阻断

**备选方案**: 强制要求 git 仓库 → 被否决，sandbox 模式应开箱即用

### Decision 4: Sandbox 模式自动 git init

**选择**: `create_conversation` 时，sandbox 模式的 workspace 目录自动 `git init` + 初始 commit。

**理由**:
- sandbox 目录（`.agenthub-data/workspaces/<conv_id>/`）是 AChat 管理的，git init 无副作用
- 使 sandbox 模式也能用真 worktree，不降级为目录拷贝
- 初始 commit 保证 worktree 创建时有干净的 HEAD

**备选方案**: sandbox 模式降级为目录拷贝 → 被否决，目录拷贝无法处理增量修改，且性能更差

### Decision 5: Worktree 目录位置 `.agenthub-data/worktrees/<conv_id>/<task_id>/`

**选择**: worktree 创建在独立的 `.agenthub-data/worktrees/` 目录下，按 conversation 和 task 分层。

**理由**:
- 与 workspace 目录分离，避免 worktree 嵌套在 workspace 内导致递归扫描
- 按 conv_id 分层便于清理（删除会话时递归清理其所有 worktree）
- 启动时扫描孤儿 worktree（`git worktree list` 对比 DB 记录）并清理

### Decision 6: `detect_wave_conflicts` 降级为 advisory，不删除

**选择**: 保留 `detect_wave_conflicts` 函数，但仅在降级模式（非 git 目录）下运行，结果注入 advisory_issues 而不阻断流程。

**理由**:
- 降级模式下仍然可能产生文件冲突，advisory 检测有诊断价值
- 不删除避免破坏已有测试，只是运行条件变化
- 未来如果引入 worktree 级别的 merge 冲突检测，可以复用这个函数的 hash 对比逻辑

### Decision 7: `_run_child_task` 通过 `ToolContext.workspace_path` 传递 worktree path

**选择**: 不新增 `ToolContext` 字段，而是把 worktree path 直接作为 `workspace_path` 传入。`get_effective_cwd` 和 `resolve_safe_path` 等现有函数无需改动——worktree path 本身就是 effective cwd。

**理由**:
- 最小改动原则——工具层不需要感知 worktree 概念
- worktree 是一个临时 workspace 目录，对工具层透明
- `ToolContext` 已经有 `workspace_path` 字段，复用即可

**备选方案**: 新增 `ToolContext.worktree_path` 字段 → 被否决，引入不必要的概念泄漏

### Decision 8: 并发安全 — per-repo mutex lock

**选择**: 同一个 git 仓库的 worktree 创建和 merge 操作需要串行化（git lockfile 不容忍并行 mutation）。用 `asyncio.Lock` keyed by workspace root path。

**理由**:
- 参考 multica-main 的 `lockForRepo` 实现
- 不同 workspace 的 worktree 操作可以并行，同一 workspace 的必须串行
- 锁粒度足够细，不影响跨会话并行

## Risks / Trade-offs

- **[Windows 兼容]** git worktree 在 Windows 上可能有路径长度问题 → Mitigation: 使用短路径名（task_id 截断到 8 字符）；测试 Windows 10/11 + git ≥ 2.15
- **[merge 冲突]** 两个并行任务改了同一文件的不同部分，git merge 可能成功但语义冲突 → Mitigation: merge 冲突时标记 `merge_conflict`，不自动解决，由 Orchestrator 在聚合时告知用户
- **[磁盘空间]** 每个 worktree 是完整的工作目录，大型项目可能占大量磁盘 → Mitigation: worktree 完成后立即清理；sandbox 模式配额仍作用于整个 workspace 树
- **[bash 写文件盲区]** bash 工具写文件不经过 `fs_write`，但 worktree 隔离后这个问题自动解决——bash 的 cwd 被强制为 worktree path → Mitigation: 确保 `ToolContext.workspace_path` 正确传递到 bash 工具的 cwd
- **[CLI adapter]** Claude CLI / Codex CLI 的 `--cwd` 参数需要正确指向 worktree path → Mitigation: 在 `build_adapter_input` 中用 worktree path 覆盖 `workspace_path`
- **[replan 跨轮]** replan 轮次间的 worktree 状态——Round 1 merge 后的主 workspace 是 Round 2 worktree 的基础 → Mitigation: 天然正确，每轮 wave 开始时基于当前主 workspace 创建 worktree

## Migration Plan

1. **Phase 1 — worktree_service 骨架（不接入运行时）**
   - 新增 `worktree_service.py`，实现 create/merge/cleanup/is_git_repo
   - sandbox 模式 git init 逻辑
   - 单元测试 worktree_service 的纯函数逻辑

2. **Phase 2 — _execute_dag 集成 worktree**
   - `_execute_dag` 中 wave 级别创建/merge/cleanup worktree
   - `_run_child_task` 传入 worktree path
   - `detect_wave_conflicts` 降级为 advisory
   - 集成测试：两任务并行写同一文件 → 不冲突

3. **Phase 3 — 前端可视化 + 清理**
   - 新增 `WorktreeEvent` SSE 事件
   - 前端 dispatch-plan-card 显示分支名和 merge 状态
   - 启动时孤儿 worktree 清理
   - E2E 手动验证

**回退策略**: worktree 创建失败时降级为无 worktree 模式（现有行为），记录 warning log。`worktree_service` 的所有调用点都有 try/except 降级路径。

## Open Questions

- 是否需要 `worktree_instances` DB 表用于崩溃恢复？当前设计用启动时扫描 `git worktree list` 清理孤儿，但如果需要在 UI 显示历史 worktree 状态则需要持久化。→ 倾向不做 DB 表，用内存 + git 自身状态管理，后续有需求再加。
- CLI adapter（Claude CLI / Codex CLI）的 worktree path 传递方式——`--cwd` 参数 vs 环境变量 vs MCP config？需要在 Phase 2 实现时验证 CLI 的具体支持。
