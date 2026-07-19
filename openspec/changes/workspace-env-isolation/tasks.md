## 1. Env 清理核心模块（阶段 1 - 止血）

- [x] 1.1 创建 `backend/app/utils/env_isolation.py`，实现 `_BLACKLISTED_KEYS` 常量（`VIRTUAL_ENV` / `PYTHONHOME` / `DATABASE_URL` / `SECRET_KEY` / `JWT_SECRET` / `REDIS_URL` / `MILVUS_HOST` / `MILVUS_PORT` / `ES_HOST` / `ES_PORT` / `NEO4J_URI` / `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_HEADERS` / `NPM_CONFIG_PREFIX` / `M2_HOME` / `GOPATH` / `GOMODCACHE`）
- [x] 1.2 实现 `_detect_achat_venv()`：通过 `sys.executable` 反推 venv 根目录（Windows 取 `Scripts` 父目录，POSIX 取 `bin` 父目录），非 venv 返回 `None`
- [x] 1.3 实现 `_clean_path(path_str, venv_to_remove)`：按 `os.pathsep` 分割 PATH，用 `os.path.normcase()` 比对删除 AChat venv 段，保留其余段
- [x] 1.4 实现 `build_tool_env(cwd=None, project_venv_path=None)`：`os.environ.copy()` → 删黑名单 key → `_clean_path()` 清理 PATH → 若 `project_venv_path` 存在则前置其 `Scripts`/`bin` 到 PATH → 返回新 env dict
- [x] 1.5 实现 `_detect_project_venv(cwd)`：检测 `<cwd>/.venv`（Windows `Scripts/python.exe`，POSIX `bin/python`），存在返回 venv 根路径，否则 `None`

## 2. bash 工具接入 env 清理（阶段 1）

- [x] 2.1 修改 `backend/app/tools/bash.py` 的 `_run_shell_command`：把 `env=os.environ.copy()` 改为 `env=build_tool_env(cwd=effective_cwd, project_venv_path=_detect_project_venv(effective_cwd))`
- [x] 2.2 确保 `effective_cwd` 从 `ToolContext` 的 workspace 路径正确传入 `_run_shell_command`
- [x] 2.3 在 bash 工具返回 stdout 前，检测命令含 `pip install` 且无项目 venv 且 `env_preference != 'system_python'` 时，追加 `[AChat]` advisory（阶段 2 完整实现，阶段 1 先占位返回不阻断）

## 3. CLI 适配器接入 env 清理（阶段 1）

- [x] 3.1 修改 `backend/app/adapters/cli_base.py` 的 `build_child_env`：先调 `env_isolation.build_tool_env(cwd, project_venv_path=None)` 获取清理 env，再叠加 `HOME`/`USERPROFILE` 和 `extra_env`
- [x] 3.2 验证 Claude Code / Codex 子进程不再继承 `VIRTUAL_ENV` 和 AChat venv PATH 段

## 4. uvicorn 热重载防护（阶段 1）

- [x] 4.1 修改 `backend/app/main.py` 的 `uvicorn.run` 调用：`reload_dirs=["app"]`（相对 backend 工作目录）+ `reload_excludes=["**/.venv/**", "**/.agenthub-data/**", "**/node_modules/**", "**/__pycache__/**"]`
- [x] 4.2 验证 `settings.debug=True` 时 `.venv` 文件变动不触发热重载，`backend/app` 下代码改动仍触发

## 5. 阶段 1 测试

- [x] 5.1 创建 `backend/tests/test_env_isolation.py`：单测 `_detect_achat_venv()`（venv / 非 venv 场景）、`_clean_path()`（Windows/POSIX PATH 清理）、`build_tool_env()`（黑名单变量删除、PATH 清理、project venv 前置）
- [x] 5.2 单测 `build_tool_env` 保留 `JAVA_HOME`/`ANDROID_SDK_ROOT` 等用户系统变量
- [x] 5.3 单测 `_detect_project_venv()` 检测 `.venv` 存在/不存在
- [x] 5.4 运行 `ruff check backend/app/utils/env_isolation.py backend/app/tools/bash.py backend/app/adapters/cli_base.py backend/app/main.py`
- [x] 5.5 运行 `pytest backend/tests/test_env_isolation.py -v`

## 6. DB schema 扩展（阶段 2）

- [x] 6.1 修改 `backend/app/db/models.py` 的 `Workspace` 模型：新增 `env_preference = Column(String, nullable=True, default=None)` 列
- [x] 6.2 创建 Alembic migration 脚本：`ALTER TABLE workspaces ADD COLUMN env_preference TEXT`（nullable，无默认值）
- [x] 6.3 更新 `backend/app/schemas/` 下 Workspace 相关 Pydantic 模型，新增 `env_preference` 字段
- [x] 6.4 更新 `specs/08-db-schema.md` 和 `specs/01-core-entities.md` 中 Workspace 字段定义

## 7. WorkspaceEnvService（阶段 2）

- [x] 7.1 创建 `backend/app/services/workspace_env_service.py`，实现 `detect_project_env(bound_path)`：检测 marker 文件（Python `requirements.txt`/`pyproject.toml`/`setup.py`，Node `package.json`，Java `pom.xml`/`build.gradle`，Go `go.mod`），返回 `ProjectEnvInfo` 对象
- [x] 7.2 实现 `detect_python_venv(bound_path)`：检测 `<bound_path>/.venv` 存在性
- [x] 7.3 实现 `detect_and_hint(conversation_id, user_id)`：调 `detect_project_env` + `detect_python_venv`，若 Python 无 venv 且 `env_preference IS NULL`，通过 EventBus 发 `WorkspaceEnvHintEvent`
- [x] 7.4 实现 `create_project_venv(bound_path)`：执行 `python -m venv --upgrade-deps .venv`，发 `WorkspaceEnvStatusEvent(creating)` → `ready`/`failed`，成功后更新 `Workspace.env_preference='venv_created'`
- [x] 7.5 在 `ConversationService` 创建 workspace 后异步触发 `detect_and_hint`（`asyncio.create_task`，不阻塞会话创建）

## 8. SSE 事件与 REST 端点（阶段 2）

- [x] 8.1 在 `backend/app/schemas/events.py` 新增 `WorkspaceEnvHintEvent`（字段：`conversationId` / `language` / `venvPresent` / `options`）和 `WorkspaceEnvStatusEvent`（字段：`conversationId` / `status` / `venvPath` / `error`）
- [x] 8.1 在 `backend/app/schemas/events.py` 新增 `WorkspaceEnvHintEvent`（字段：`conversationId` / `language` / `venvPresent` / `options`）和 `WorkspaceEnvStatusEvent`（字段：`conversationId` / `status` / `venvPath` / `error`）
- [x] 8.2 在 `src/shared/` 新增对应 TypeScript 类型定义（camelCase 字段兼容）
- [x] 8.3 新增 `POST /api/workspaces/{conversation_id}/create-venv` 端点：鉴权 + 校验 workspace 归属 + 调 `WorkspaceEnvService.create_project_venv`
- [x] 8.4 新增 `PATCH /api/workspaces/{conversation_id}/env-preference` 端点：接收 `preference` body，更新 `Workspace.env_preference`
- [x] 8.5 新增 `GET /api/workspaces/{conversation_id}/env-status` 端点：返回当前环境检测结果和 `env_preference`
- [x] 8.6 确保新端点都有 `get_current_user` 依赖和 CSRF Origin 校验

## 9. bash 工具 pip advisory 完善（阶段 2）

- [x] 9.1 在 `bash.py` 完整实现 pip install advisory：读 `Workspace.env_preference`，检测 `pip install` 正则，无 venv 且 `env_preference != 'system_python'` 时追加 `[AChat]` advisory 到 stdout
- [x] 9.2 advisory 格式：`[AChat Env Advisory] 检测到 pip install 但项目无 .venv。包将安装到系统 Python（AChat 运行环境已隔离）。建议创建项目 venv。`，用分隔符与命令实际输出分离
- [x] 9.3 单测 advisory 触发条件（有 venv 不触发 / `system_python` 不触发 / 无 venv 触发）

## 10. 前端实现（阶段 2）

- [x] 10.1 在 `src/shared/types.ts` 新增 `WorkspaceEnvHintEvent` / `WorkspaceEnvStatusEvent` 类型
- [x] 10.2 在 SSE store reducer 新增 `workspace_env_hint` / `workspace_env_status` 事件处理，按 `conversationId` 存储 hint 状态，幂等应用
- [x] 10.3 创建 `src/components/WorkspaceEnvHintCard.tsx`：banner 样式，三个按钮（Create .venv / Skip / Use system Python），根据 `workspace_env_status` 切换 creating/ready/failed 状态
- [x] 10.4 在会话视图顶部条件渲染 `WorkspaceEnvHintCard`（当 store 有该 conversation 的 hint 时）
- [x] 10.5 Create 按钮调 `POST /api/workspaces/{id}/create-venv`，Skip/system_python 调 `PATCH /api/workspaces/{id}/env-preference`
- [x] 10.6 failed 状态显示错误信息 + Retry 按钮

## 11. 阶段 2 测试与收尾

- [x] 11.1 后端单测 `WorkspaceEnvService.detect_project_env`（各语言 marker 文件检测）
- [x] 11.2 后端单测 `create_project_venv` 成功/失败路径（mock subprocess）
- [x] 11.3 后端单测新 REST 端点（鉴权、归属校验、CSRF）
- [x] 11.4 后端集成测试：workspace 创建 → 检测 → hint 事件 → create-venv → bash venv 前置
- [x] 11.5 前端 `pnpm typecheck` + `pnpm lint` 通过
- [x] 11.6 后端 `ruff check .` + `pytest` 通过
- [x] 11.7 更新 `backend/.env.example`（如有新增配置项）
- [x] 11.8 更新 `specs/11-platform.md` 的「命令黑名单」节，补充 env 变量黑名单契约
- [ ] 11.9 手动 E2E 验证：本地项目跑 agent `pip install`，确认包装到项目 venv 而非 AChat venv，AChat 服务不崩溃
