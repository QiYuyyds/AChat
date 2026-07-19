## Why

Agent 的 bash 工具通过 `os.environ.copy()` 继承了 AChat 后端进程的完整环境（包括 AChat 自己的 `.venv`、`DATABASE_URL`、`SECRET_KEY` 等内部变量）。当 agent 在绑定的本地项目里执行 `pip install` 时，pip 解析到 AChat venv 的 pip，包装到 AChat 的 `.venv/Lib/site-packages/`，大量文件变动触发 uvicorn 热重载，最终导致服务崩溃（`ModuleNotFoundError: No module named 'uvicorn'`）。

这是 Claude Code 式本地仓库开发工作流的根本性工程障碍：AChat 作为「平台跑 agent」的形态，必须比「CLI 直接跑 agent」更重视环境隔离——agent 的 bash 永远不能污染 AChat 自己的运行时。

## What Changes

- **bash 子进程 env 清理（止血层）**：新增 `backend/app/utils/env_isolation.py`，bash 工具不再 `os.environ.copy()`，而是通过 `build_tool_env()` 构建清理后的 env——移除 AChat venv 标记（`VIRTUAL_ENV` / `PYTHONHOME`）、从 PATH 删除 AChat venv 的 `Scripts/bin` 段、移除 AChat 内部敏感变量（`DATABASE_URL` / `SECRET_KEY` / `REDIS_URL` / `MILVUS_HOST` / `OTEL_*` 等）、移除 Node/Java/Go 全局污染变量（`NPM_CONFIG_PREFIX` / `M2_HOME` / `GOPATH` 等）。CLI 适配器的 `build_child_env` 同步复用此清理逻辑。
- **uvicorn 热重载防护**：`main.py` 的 `uvicorn.run` 新增 `reload_dirs=["app"]` 和 `reload_excludes=[".venv", ".agenthub-data", "node_modules", "__pycache__"]`，防止 `.venv` 等非代码目录变动触发热重载。
- **项目环境检测（根治层）**：新增 `backend/app/services/workspace_env_service.py`，workspace 绑定本地目录时异步检测项目语言和环境状态（Python `.venv` / Node `package.json` / Java `pom.xml` / Go `go.mod`）。
- **Python venv 创建引导（问用户）**：检测到 Python 项目但没有 venv 时，通过 SSE `workspace_env_hint` 事件通知前端显示提示卡片，用户可选择「创建 `.venv`」/「跳过」/「使用系统 Python」。用户选择「创建」时后端执行 `python -m venv .venv`，成功后 bash 子进程 PATH 前置项目 venv。
- **bash 工具 venv 前置**：bash 执行时检测项目 venv，存在则 PATH 前置 venv 的 `bin/Scripts`，使 `pip install` 装到项目 venv 而非系统 python。
- **bash 工具 pip advisory（兜底）**：bash 检测到 `pip install` 命令且项目无 venv 且用户未选择「系统 Python」时，返回 advisory 警告引导 agent 先创建 venv（不阻断，env 清理层已兜底防污染）。
- **Workspace 模型扩展**：`Workspace` 表新增 `env_preference` 字段（`venv_created` / `skip` / `system_python` / `null`），记录用户的环境选择，避免重复提示。
- **新增 SSE 事件**：`WorkspaceEnvHintEvent`（workspace 环境提示）和 `WorkspaceEnvStatusEvent`（venv 创建进度/结果）。

## Capabilities

### New Capabilities

- `workspace-env-isolation`: Workspace 级别的环境隔离能力——bash 子进程 env 清理、项目环境检测、Python venv 创建引导、PATH 前置策略、多语言隔离机制。

### Modified Capabilities

- `tools`: bash 工具的 env 构造从 `os.environ.copy()` 改为 `build_tool_env()`；bash 执行时检测项目 venv 并 PATH 前置；bash 检测到 `pip install` 无 venv 时返回 advisory。
- `platform-security`: env 清理的安全约束——哪些变量必须移除（AChat 内部 + 全局污染）、哪些必须保留（`HOME` / `USERPROFILE` / `SystemRoot` / `LANG`）；uvicorn 热重载的目录排除策略。
- `stream-events`: 新增 `WorkspaceEnvHintEvent` 和 `WorkspaceEnvStatusEvent` 事件类型。
- `frontend`: 新增 workspace 环境提示卡片组件（检测到 Python 项目无 venv 时显示「创建 .venv」/「跳过」/「使用系统 Python」选项）。

## Impact

- **后端新增**：
  - `backend/app/utils/env_isolation.py` — env 清理核心逻辑（`build_tool_env()` / `is_internal_env_key()` / `_clean_path()` / `_detect_achat_venv()`）。
  - `backend/app/services/workspace_env_service.py` — 项目环境检测（`detect_project_env()` / `detect_python_venv()`）+ venv 创建（`create_project_venv()`）。
- **后端修改**：
  - `backend/app/tools/bash.py` — `_run_shell_command` 的 env 从 `os.environ.copy()` 改为 `build_tool_env(cwd, project_venv_path)`；新增 pip install advisory 检测。
  - `backend/app/adapters/cli_base.py` — `build_child_env` 复用 `env_isolation` 的清理逻辑（移除 `VIRTUAL_ENV` / PATH venv 段）。
  - `backend/app/main.py` — `uvicorn.run` 加 `reload_dirs` / `reload_excludes`。
  - `backend/app/services/conversation_service.py` — workspace 创建后触发异步环境检测。
  - `backend/app/api/` — 新增 `POST /api/workspaces/{conversation_id}/create-venv` 和 `GET /api/workspaces/{conversation_id}/env-status`。
  - `backend/app/schemas/events.py` — 新增 `WorkspaceEnvHintEvent` / `WorkspaceEnvStatusEvent`。
  - `backend/app/db/models.py` — `Workspace` 表新增 `env_preference` 列（nullable，default null）。
- **前端**：
  - `src/shared/types.ts` — 新增事件类型定义。
  - `src/components/` — 新增 `WorkspaceEnvHintCard` 组件。
  - `src/stores/` — SSE reducer 处理新事件。
- **DB migration**：`Workspace` 表新增 `env_preference TEXT` 列，nullable，无默认值（null = 未检测）。
- **依赖**：无新第三方依赖。venv 创建用 `python -m venv`（stdlib）；环境检测用 `os.path` / `pathlib`（stdlib）。
- **向后兼容**：
  - bash 工具的 env 清理对外部无感（agent 不该依赖 AChat 的内部环境变量）。
  - `env_preference` 是 nullable 新列，不影响现有 workspace。
  - venv 创建引导是异步提示，不阻塞会话创建和对话。
  - uvicorn `reload_dirs` 只影响开发模式（`settings.debug=True`），生产模式无变化。
