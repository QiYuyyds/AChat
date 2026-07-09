## 1. WorktreeService 核心实现

- [x] 1.1 新增 `backend/app/services/worktree_service.py`，定义 `WorktreeRef` dataclass：`task_id`, `branch_name`, `path`, `main_workspace_path`, `is_git`
- [x] 1.2 实现 `is_git_repo(path: str) -> bool`：检查路径下是否存在 `.git` 目录或 `.git` 文件
- [x] 1.3 实现 `ensure_git_init(workspace_path: str) -> bool`：对非 git 目录执行 `git init` + 初始空 commit + 创建 `.gitignore`（排除 `.agenthub-data/`）；返回是否成功
- [x] 1.4 实现 `sanitize_agent_name(name: str) -> str`：转小写、空格转 `-`、移除非字母数字字符，截断到 30 字符
- [x] 1.5 实现 `create_worktree(main_workspace: str, task_id: str, agent_name: str, conversation_id: str) -> WorktreeRef | None`：
  - git 模式：`git worktree add -b agent/{sanitized}/{short_task_id} {worktree_path} HEAD`
  - 非 git 降级：`shutil.copytree(main_workspace, worktree_path)`
  - 失败返回 None（调用方降级）
- [x] 1.6 实现 `merge_worktree_back(wt: WorktreeRef) -> MergeResult`：
  - git 模式：worktree 内 `git add -A && git commit -m "task {task_id}"`，主 workspace `git merge --no-edit {branch}`
  - 非 git 降级：`shutil.copy2` 遍历复制 worktree 文件到主 workspace（覆盖）
  - 返回 `MergeResult(success, conflict_files, error)`
- [x] 1.7 实现 `cleanup_worktree(wt: WorktreeRef) -> None`：
  - git 模式：`git worktree remove --force {path}` + `git branch -D {branch}`
  - 非 git：`shutil.rmtree(path, ignore_errors=True)`
  - 幂等——路径不存在时静默返回
- [x] 1.8 实现 `prune_orphan_worktrees(worktrees_root: str) -> list[str]`：扫描 `worktrees_root` 下所有目录，检查是否有对应 active run，无则清理；返回清理的 worktree 列表
- [x] 1.9 单元测试：`is_git_repo` 正确识别 git/非 git 目录；`sanitize_agent_name` 处理特殊字符、空格、中文；`create_worktree` git 模式创建正确的分支和目录；`create_worktree` 非 git 降级为 copytree；`merge_worktree_back` git 模式成功合并；`merge_worktree_back` merge 冲突时返回 conflict_files；`cleanup_worktree` 幂等；`prune_orphan_worktrees` 清理无主目录

## 2. 并发安全锁

- [x] 2.1 在 `worktree_service.py` 中实现 `_WorktreeLockManager` 类：per-workspace-path `asyncio.Lock`，不同 workspace 并行、同一 workspace 串行
- [x] 2.2 `create_worktree` 和 `merge_worktree_back` 调用时获取对应 workspace 的锁
- [x] 2.3 单元测试：同一 workspace 的两个 create_worktree 调用串行执行；不同 workspace 的调用并行执行

## 3. Sandbox 模式 git init

- [x] 3.1 在 `backend/app/services/conversation_service.py` 的 `create_conversation` 中，sandbox 模式 workspace 目录创建后调用 `ensure_git_init(root_path)`
- [x] 3.2 `ensure_git_init` 失败时不阻断会话创建，记录 warning log，workspace 标记为非 git（后续 worktree 降级为 copytree）
- [x] 3.3 单元测试：sandbox 模式创建会话后 workspace 是 git 仓库（有 HEAD commit）；git init 失败时会话仍创建成功

## 4. _execute_dag 集成 worktree

- [x] 4.1 在 `backend/app/services/orchestrator.py` 的 `_execute_dag` 中，wave 开始前为 `wave_tasks` 中每个 task 调用 `create_worktree`，收集 `WorktreeRef`（失败则该 task 降级为无 worktree）
- [x] 4.2 将 worktree path 传入 `_run_child_task` 的新参数 `worktree_path: str | None`；`worktree_path` 非 None 时作为 `ToolContext.workspace_path` 和 `build_sub_agent_prompt` 的 workspace path
- [x] 4.3 wave 结束后，遍历 wave 结果：`complete` 状态的 task 调用 `merge_worktree_back`；所有 task（无论状态）调用 `cleanup_worktree`
- [x] 4.4 merge 冲突时（`MergeResult.success == False`）：task 状态改为 `merge_conflict`，冲突文件列表注入 `ctx` 的 conflict 记录，由聚合阶段上报
- [x] 4.5 `detect_wave_conflicts` 调用条件改为：仅当 wave 中有 task 降级为无 worktree 时才运行，且结果作为 advisory（不阻断）
- [x] 4.6 wave 中所有 task 都有 worktree 时，跳过 `detect_wave_conflicts` 调用
- [x] 4.7 集成测试：两个 mock task 并行写同一文件 `src/config.ts`（不同内容），worktree 模式下不冲突，merge 后主 workspace 包含两个文件的各自版本（或 merge 冲突被正确标记）
- [x] 4.8 集成测试：worktree 创建失败降级时，`detect_wave_conflicts` 作为 advisory 运行

## 5. _run_child_task worktree path 传递

- [x] 5.1 修改 `_run_child_task` 签名，新增 `worktree_path: str | None = None` 参数
- [x] 5.2 `worktree_path` 非 None 时：`ToolContext.workspace_path = worktree_path`；`build_sub_agent_prompt` 传入 worktree path 作为 workspace
- [x] 5.3 harness loop 的 attempt 循环中，所有 attempt 使用同一个 `worktree_path`（不重新创建 worktree）
- [x] 5.4 `worktree_path` 为 None 时（降级模式），行为与现有完全一致（使用主 workspace path）
- [x] 5.5 单元测试：`_run_child_task` 传入 worktree_path 时，ToolContext.workspace_path 等于 worktree_path；传入 None 时等于主 workspace path

## 6. CLI Adapter worktree path 传递

- [x] 6.1 在 `backend/app/services/agent_runner.py` 的 `build_adapter_input` 中，当 `worktree_path` 非 None 时，用它覆盖 `AdapterInput.workspace_path`
- [x] 6.2 ClaudeCLIAdapter：确保 `--cwd` 参数（或 `extra_env` 中的 cwd 设置）指向 worktree path
- [x] 6.3 CodexCLIAdapter：确保 JSON-RPC 通信中的 cwd 指向 worktree path
- [x] 6.4 验证 CLI adapter 在 worktree 中执行时，`fs_write` / `bash` 工具的路径解析正确落在 worktree 子树内

## 7. WorktreeEvent SSE 事件

- [x] 7.1 在 `backend/app/schemas/events.py` 中新增 `WorktreeEvent(BaseEvent)`：`type: Literal["worktree.created" | "worktree.merged" | "worktree.cleaned"]`，字段 `task_id`, `branch_name`, `path`, `merge_status`（仅 merged 类型）
- [x] 7.2 在 `src/shared/types.ts` 中新增对应的 `WorktreeEvent` 类型，并加入 `StreamEvent` 联合类型
- [x] 7.3 在 `worktree_service.py` 的 `create_worktree` / `merge_worktree_back` / `cleanup_worktree` 成功后发布对应 `WorktreeEvent`（通过 EventBus）
- [x] 7.4 单元测试：create/merge/cleanup 各发布正确类型的 WorktreeEvent，字段正确

## 8. 启动时孤儿 worktree 清理

- [x] 8.1 在 `backend/app/main.py` 的 startup 事件中调用 `prune_orphan_worktrees(WORKTREES_ROOT)`
- [x] 8.2 同时调用 `git worktree prune` 清理 git 层面的过期 worktree 元数据（针对主 workspace 仓库）
- [x] 8.3 清理完成后记录 summary log（清理了多少个孤儿 worktree）
- [x] 8.4 单元测试：模拟 `.agenthub-data/worktrees/conv_xxx/t1/` 目录存在但无对应 active run，`prune_orphan_worktrees` 正确清理

## 9. 前端可视化

- [x] 9.1 在 `src/stores/app-store.ts` 的 SSE 事件 reducer 中新增 `case 'worktree.created'` / `'worktree.merged'` / `'worktree.cleaned'` 分支
- [x] 9.2 在 run state 中新增 `worktreeByTask: Record<string, { branchName, path, mergeStatus }>` 字段
- [x] 9.3 在 `src/components/dispatch-plan-card.tsx`（或 `dispatch/` 目录下的调度卡组件）中，当 task 有 worktree 数据时显示分支名 badge（如 `🌿 agent/x/t1`）
- [x] 9.4 merge 成功后显示 ✓ merge 标记；merge 冲突显示 ⚠️ merge conflict 标记
- [x] 9.5 worktree.cleaned 后清除该 task 的 worktree 数据
- [x] 9.6 单元测试：worktree.created 事件更新 store；worktree.merged 事件更新 mergeStatus；worktree.cleaned 事件清除数据

## 10. Spec 文档同步

- [x] 10.1 在 `specs/06-orchestrator-flow.md` 新增 "Worktree 隔离" 章节：worktree 生命周期与 wave 对齐、merge 策略、降级路径
- [x] 10.2 更新 `specs/06-orchestrator-flow.md` 的 "代码冲突检测" 章节：标注 worktree 模式下不运行，降级模式下作为 advisory
- [x] 10.3 更新 `specs/01-core-entities.md` 的 Workspace 章节：sandbox 模式自动 git init 的行为说明
- [x] 10.4 更新 `specs/11-platform.md`：新增 worktree 目录路径安全检查、孤儿清理相关内容

## 11. 集成验证

- [x] 11.1 后端 `ruff check .` 通过
- [x] 11.2 后端 `pytest` 通过（新增单元测试 + 集成测试全绿）
- [x] 11.3 前端 `pnpm typecheck` 通过
- [x] 11.4 前端 `pnpm lint` 通过
- [ ] 11.5 手动验证：创建群聊（Orchestrator + 2 个 custom agent），sandbox 模式，发送"同时生成一个前端页面和一个后端 API"；验证两个子任务各自在独立 worktree 中执行，完成后 merge 回主 workspace
- [ ] 11.6 手动验证：同一场景，让两个子任务都写 `package.json`；验证 worktree 隔离下不冲突，merge 时正确处理（成功或冲突标记）
- [ ] 11.7 手动验证：harness loop 重试时，attempt 2 能看到 attempt 1 写的文件（在同一个 worktree 内）
- [ ] 11.8 手动验证：local 模式绑定真实 git 仓库，验证 worktree 创建在 `.agenthub-data/worktrees/` 下，merge 回主分支正确
- [ ] 11.9 手动验证：强制 worktree 创建失败（如权限问题），验证降级为无 worktree 模式，`detect_wave_conflicts` 作为 advisory 运行
- [ ] 11.10 手动验证：启动时存在孤儿 worktree 目录，验证启动后自动清理
- [x] 11.11 回归测试：现有 `pytest tests/test_orchestrator.py tests/test_dispatch_plan.py` 通过
