## Context

AChat 后端以 FastAPI 进程运行，默认在仓库根目录的 `.venv` 里启动（`pip install -e .` 装在 `.venv/Lib/site-packages/`）。Agent 的 bash 工具通过 `os.environ.copy()` 把后端进程的完整环境交给子进程，导致两个严重问题：

1. **PATH 污染**：子进程的 `PATH` 头部是 AChat `.venv/Scripts`（Windows）/ `.venv/bin`（POSIX），`python` / `pip` 都指向 AChat venv。agent 在用户项目里跑 `pip install foo`，包被装进 AChat venv 而非项目 venv。
2. **敏感变量泄露**：`DATABASE_URL` / `SECRET_KEY` / `REDIS_URL` / `MILVUS_HOST` / `OTEL_EXPORTER_OTLP_ENDPOINT` 等 AChat 内部变量流入子进程，agent 可读取，破坏隔离。

附带问题：开发模式下 `uvicorn --reload` 监听整个项目根，`.venv` 文件变动触发热重载，`pip install` 后 AChat 服务崩溃。

CLI 适配器（Claude Code / Codex）也有同样问题：`build_child_env` 虽然设了 `HOME`/`USERPROFILE` 隔离用户认证，但 PATH 和 `VIRTUAL_ENV` 仍继承自后端进程。

### 约束

- CLAUDE.md §3.1：基础设施服务必须经过 `infra/factory.py`；L3 服务不直接 new 客户端。env 清理是工具层逻辑，属 L3/L2，不碰基础设施。
- CLAUDE.md §5.3：workspace 沙箱有 `local` 和 `sandbox` 两种模式，路径必须落在 effective cwd 子树内。env 清理与沙箱路径正交，不冲突。
- CLAUDE.md §5.5：CLI Agent 隔离已设 `HOME`/`USERPROFILE`，env 清理在此之上补充 PATH / `VIRTUAL_ENV` 清理。
- 双平台：Windows 用 `Scripts`，POSIX 用 `bin`，env 清理必须双平台。
- 用户决策：「没有 venv 时的创建策略是问用户是否需要创建」——不自动创建，通过 SSE 提示卡片让用户选。

### 利益相关方

- 终端用户：本地绑定项目后跑 agent，希望 `pip install` 装到项目而非 AChat。
- AChat 平台：必须保证 agent 子进程不污染自身运行时。
- 前端：需要展示环境提示卡片和处理用户选择。

## Goals / Non-Goals

**Goals:**

- G1：bash 工具子进程的 env 不再包含 AChat venv 标记和内部敏感变量，`pip install` 永远装不到 AChat venv。
- G2：bash 工具执行时若项目有 venv，PATH 自动前置项目 venv，`python`/`pip` 指向项目 venv。
- G3：workspace 绑定本地项目时检测项目语言和环境状态；Python 项目无 venv 时通过 SSE 提示用户选择「创建 .venv」/「跳过」/「使用系统 Python」。
- G4：uvicorn 开发模式热重载只监听 `app/` 目录，排除 `.venv` / `.agenthub-data` / `node_modules`。
- G5：CLI 适配器子进程复用同一套 env 清理逻辑。
- G6：env 清理逻辑可单元测试，不依赖真实环境。

**Non-Goals:**

- N1：不自动为用户项目创建 venv（由用户通过提示卡片选择）。
- N2：不管理 Node.js / Java / Go 的包管理器安装（`nvm` / `sdkman` / `gvm` 由用户系统负责）；仅清理会污染的全局变量（`NPM_CONFIG_PREFIX` / `M2_HOME` / `GOPATH`）。
- N3：不做 Docker 容器级隔离（成本过高，本地运行场景过重）。
- N4：不修改 `fs_read`/`fs_write` 工具的 env（它们不执行任意命令，无污染风险）。
- N5：不处理 conda 环境（用户若用 conda，其 activate 脚本会自己设 `CONDA_PREFIX`；env 清理不主动移除 conda 变量，仅清理 AChat venv 标记）。
- N6：不做 Windows PATH 大小写敏感的全量归一化（Windows PATH 不区分大小写，清理时按小写匹配即可）。

## Decisions

### D1：env 清理采用「白名单 + 黑名单」组合，而非纯白名单

**决策**：`build_tool_env()` 先 `os.environ.copy()`，然后（a）移除黑名单 key（`VIRTUAL_ENV` / `PYTHONHOME` / `DATABASE_URL` / `SECRET_KEY` / `REDIS_URL` / `MILVUS_HOST` / `MILVUS_PORT` / `ES_HOST` / `NEO4J_URI` / `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_HEADERS` / `NPM_CONFIG_PREFIX` / `M2_HOME` / `GOPATH` / `GOMODCACHE` 等），（b）从 `PATH` 中删除 AChat venv 的 `Scripts`/`bin` 段，（c）保留 `HOME`/`USERPROFILE`/`SystemRoot`/`LANG`/`PATH`（清理后）/`TEMP`/`TMP` 等系统必需变量。

**理由**：纯白名单（只保留已知安全变量）会破坏用户项目依赖的系统变量（如 `JAVA_HOME`、`ANDROID_SDK_ROOT`、自定义 `PATH` 段）。黑名单精确移除已知危险变量，保留用户项目需要的其余变量，更符合「agent 在用户机器上干活」的语义。

**替代方案**：
- 纯白名单：太激进，会破坏 agent 在用户项目里的正常工作（如跑 `mvn` 需要 `JAVA_HOME`）。拒绝。
- 完全独立最小 env（只有 `PATH=/usr/bin:/bin`）：丢失用户系统配置，agent 无法用用户安装的工具。拒绝。

### D2：AChat venv 路径检测用 `sys.executable` 反推，而非硬编码

**决策**：`_detect_achat_venv()` 通过 `sys.executable`（后端进程的 Python 解释器路径）反推 venv 根目录——Windows 下 `sys.executable` 是 `<venv>/Scripts/python.exe`，POSIX 下是 `<venv>/bin/python`，取父目录即 venv 根。然后从子进程 `PATH` 中删除该 venv 的 `Scripts`/`bin` 段。

**理由**：硬编码 `.venv` 不可靠（用户可能用 `venv` / `.venv-310` / 自定义名）。`sys.executable` 是当前进程的真实路径，一定能定位到 AChat 自己的 venv。

**替代方案**：
- 读 `VIRTUAL_ENV` 环境变量：只在 venv 被 activate 时存在，`uvicorn` 直接用 `.venv/bin/python` 启动时不一定有。拒绝。
- 硬编码项目根的 `.venv`：太脆弱。拒绝。

### D3：项目 venv 检测和 PATH 前置在 bash 工具执行时实时做，而非缓存

**决策**：bash 工具每次执行时调用 `_detect_project_venv(cwd)` 检测 `<cwd>/.venv`（Windows `Scripts/python.exe`，POSIX `bin/python`），存在则把 `<venv>/Scripts`（或 `bin`）前置到清理后的 `PATH`。

**理由**：用户可能在对话过程中手动创建 venv 或通过提示卡片创建，缓存会过期。检测成本极低（一次 `os.path.exists`），不值得缓存。实时检测保证一致性。

**替代方案**：
- workspace 创建时检测一次并缓存：用户后续创建 venv 不生效。拒绝。
- 读 `Workspace.env_preference`：该字段记录用户选择，不保证 venv 实际存在（用户可能手动删了）。检测文件系统是 source of truth。

### D4：Python venv 创建引导用 SSE 事件 + 前端卡片，而非 ask_user 工具

**决策**：workspace 绑定本地项目后，`ConversationService` 异步调用 `WorkspaceEnvService.detect_and_hint()`。检测到 Python 项目（有 `requirements.txt` / `pyproject.toml` / `setup.py`）且无 `.venv` 时，通过 SSE 推送 `WorkspaceEnvHintEvent`。前端显示提示卡片，用户点击「创建 .venv」后前端调 `POST /api/workspaces/{conversation_id}/create-venv`，后端执行 `python -m venv .venv` 并通过 `WorkspaceEnvStatusEvent` 反馈进度/结果。

**理由**：
- `ask_user` 工具是 agent 主导的交互，在对话流中插入；而 venv 创建是 workspace 级别的一次性引导，不该占用对话流，且应在 agent 开始干活前完成。
- SSE 事件 + REST 端点是 AChat 已有的前端通信模式（见 `specs/stream-events/spec.md`），复用现有机制成本最低。
- 用户选择记录到 `Workspace.env_preference`，避免重复提示。

**替代方案**：
- `ask_user` 工具：占用对话流，且 agent 不一定主动问。拒绝。
- 自动创建 venv：用户明确要求「问用户是否需要创建」，自动创建违背用户意图。拒绝。
- 前端检测：前端无法访问用户本地文件系统（浏览器沙箱），必须后端检测。拒绝。

### D5：`Workspace.env_preference` 用 nullable 字符串，而非枚举表

**决策**：`Workspace` 表新增 `env_preference TEXT NULL`，取值为 `null`（未检测）/ `'venv_created'` / `'skip'` / `'system_python'`。不新建枚举表。

**理由**：取值集合小且封闭，nullable 字符串足够。新增枚举表增加 join 成本和迁移复杂度，不值。

**替代方案**：
- 新建 `WorkspaceEnvPreference` 枚举表：过度设计。拒绝。
- 用 JSONB 字段存更多元数据（venv 路径、Python 版本）：当前需求不需要，YAGNI。

### D6：uvicorn reload 配置用 `reload_dirs` + `reload_excludes`，而非 `reload_includes`

**决策**：`main.py` 的 `uvicorn.run(reload=settings.debug, reload_dirs=["backend/app"], reload_excludes=["**/.venv/**", "**/.agenthub-data/**", "**/node_modules/**", "**/__pycache__/**"])`。

**理由**：
- `reload_dirs` 限定监听目录，比 `reload_includes`（glob 匹配文件）更精确——只听 `backend/app` 代码，`.venv` 在项目根不在 `backend/app` 下自然被排除。
- `reload_excludes` 额外排除 `__pycache__` 等 `backend/app` 下可能产生的非代码目录。
- 双保险，确保 `.venv` 变动永不触发热重载。

**替代方案**：
- 只用 `reload_excludes`：如果用户把 venv 建在 `backend/app` 下（不太可能但理论可行）仍会触发。`reload_dirs` 收窄监听范围更稳。
- 改成生产模式不 reload：开发体验差。保留 reload 但收窄范围。

### D7：CLI 适配器复用 `env_isolation` 清理逻辑，而非各自实现

**决策**：`backend/app/adapters/cli_base.py` 的 `build_child_env` 调用 `env_isolation.build_tool_env(cwd, project_venv_path=None)` 获取清理后的 env，然后在其之上叠加 CLI 特定的变量（`HOME`/`USERPROFILE` 用户隔离、`extra_env`）。

**理由**：DRY。env 清理逻辑只有一份，bash 和 CLI 共享，避免逻辑漂移。

**替代方案**：CLI 各自实现清理：重复代码，易不一致。拒绝。

### D8：pip install advisory 不阻断，仅警告

**决策**：bash 工具检测到命令包含 `pip install` 且项目无 venv 且 `Workspace.env_preference != 'system_python'` 时，在 stdout 返回前追加一段 advisory 警告（`[AChat] 检测到 pip install 但项目无 .venv，建议先创建 venv。本次已自动隔离 AChat 运行环境，包将装到系统 Python。`），不阻断命令执行。

**理由**：
- env 清理层（D1）已经兜底：即使装到系统 Python，也不会污染 AChat。advisory 是用户体验优化，不是安全防线。
- 阻断会破坏 agent 的自主性（agent 可能确实想用系统 Python）。
- 用户已通过 D4 的提示卡片做过选择，advisory 是对未选择「创建」或忽略卡片的用户的二次提醒。

**替代方案**：
- 阻断 pip install 直到创建 venv：破坏 agent 自主性，且用户可能就想用系统 Python。拒绝。
- 静默不提示：用户不知道包装到哪了，体验差。拒绝。

## Risks / Trade-offs

- **[Risk] env 清理误删用户项目需要的变量** → 黑名单只移除已知 AChat 内部变量和全局污染变量，保留用户系统变量；黑名单内容在 `specs/platform-security/spec.md` 固化为契约，修改需同步 spec。单测覆盖黑名单。
- **[Risk] `sys.executable` 反推 venv 路径在非 venv 环境（系统 Python 直接跑）失败** → `_detect_achat_venv()` 返回 `None`，`_clean_path()` 无 venv 段可删，env 清理仍生效（黑名单变量照删）。功能降级而非报错。
- **[Risk] venv 创建失败（磁盘满 / 权限 / Python 缺失）** → `create_project_venv()` 捕获 `subprocess.CalledProcessError`，通过 `WorkspaceEnvStatusEvent` 推送失败原因，前端卡片显示错误并允许重试。不阻断对话。
- **[Risk] `Workspace.env_preference` 与实际 venv 状态不一致**（用户手动删了 venv）→ bash 工具每次执行实时检测 venv 文件（D3），`env_preference` 仅控制是否重复提示，不影响 PATH 前置逻辑。
- **[Risk] uvicorn `reload_dirs` 遗漏非 `backend/app` 下的代码改动**（如 `backend/tests` 或根目录配置）→ 开发者可手动重启；生产模式不受影响。权衡：接受少量开发体验损失换取 `.venv` 污染根治。
- **[Trade-off] advisory 警告可能被 agent 当作命令输出误读** → advisory 用 `[AChat]` 前缀和明确分隔符标注，与命令实际输出分离；单测验证格式。
- **[Risk] Windows PATH 大小写** → 清理时对 PATH 段做 `os.path.normcase()` 比对（Windows 下小写化），删除时操作原始段。单测覆盖 Windows 路径。
- **[Risk] conda 环境**（N6）→ 不主动清理 conda 变量；若用户在 conda env 里跑 AChat 且 agent 项目无 venv，`pip install` 可能装到 conda env。这是用户主动选择 conda 的预期行为，不处理。

## Migration Plan

1. **阶段 1（止血，可独立合并）**：
   - 新增 `backend/app/utils/env_isolation.py`。
   - 修改 `backend/app/tools/bash.py` 使用 `build_tool_env()`。
   - 修改 `backend/app/adapters/cli_base.py` 复用清理逻辑。
   - 修改 `backend/app/main.py` 加 `reload_dirs`/`reload_excludes`。
   - 单测：`backend/tests/test_env_isolation.py`。
   - 此阶段独立合并后立即生效，`.venv` 污染问题即根治。

2. **阶段 2（根治 + 体验，依赖阶段 1）**：
   - DB migration：`Workspace` 表加 `env_preference` 列。
   - 新增 `backend/app/services/workspace_env_service.py`。
   - 新增 SSE 事件 `WorkspaceEnvHintEvent` / `WorkspaceEnvStatusEvent`。
   - 新增 REST 端点 `POST /api/workspaces/{conversation_id}/create-venv` / `GET /api/workspaces/{conversation_id}/env-status`。
   - 修改 `ConversationService` 在 workspace 创建后触发异步检测。
   - 前端：事件类型、提示卡片组件、SSE reducer、API 调用。
   - bash 工具加 pip install advisory。
   - 单测 + 集成测试。

3. **回滚策略**：
   - 阶段 1 回滚：revert bash.py / cli_base.py / main.py 改动，无 DB 变更，安全。
   - 阶段 2 回滚：`env_preference` 列保留（nullable，无害），revert 服务/前端/事件代码。已创建的 venv 文件不回滚（用户项目资产）。

## Open Questions

- Q1：venv 创建用 `python -m venv` 还是 `python -m venv --upgrade-deps`（自动升级 pip）？倾向后者，但 `--upgrade-deps` 在 Python 3.9+ 才有。需确认 AChat 支持的最低 Python 版本（CLAUDE.md 写 Python 3.11+，可用）。
- Q2：提示卡片在对话流中如何展示？是顶部固定 banner 还是消息流内 inline？倾向顶部 banner（workspace 级别引导，不属于对话内容），但需前端确认 UX。
- Q3：是否需要在 workspace 创建时同步检测 Node/Java/Go 环境？当前 proposal 只检测 Python（因为污染问题最严重），其他语言仅清理全局变量。是否要为 Node 项目也做 `nvm use` 引导？倾向暂不做（N2），后续按需迭代。
