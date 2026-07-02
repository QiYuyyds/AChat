# Tasks: migrate-claude-codex-to-cli

## Phase 1: 公共基础设施（CLI 基类 + 模型/输入扩展）

- [x] **T1.1** 新增 `backend/app/adapters/cli_base.py`
  - `CLIProcess`: 封装 CLI 子进程生命周期（启动、关闭、优雅退出）
  - `CLIAdapterBase`: CLI 适配器抽象基类
  - `filter_custom_args()`: 过滤 blocked 参数（参考 multica `filterCustomArgs`）
  - `is_filtered_child_env_key()`: 环境变量剥离
  - `merge_env()`: 环境变量合并
  - 测试：`test_cli_base.py` 覆盖进程关闭流程、参数过滤、环境变量剥离

- [x] **T1.2** 修改 `backend/app/adapters/base.py`
  - `AdapterInput` 新增 5 个可选字段：`executable_path`, `extra_env`, `custom_args`, `resume_session_id`, `mcp_config`
  - 全部默认 None，保持向后兼容

- [x] **T1.3** 修改 `backend/app/db/models.py`
  - `Agent` 新增 `executable_path` (String, nullable)
  - `Agent` 新增 `protocol_family` (String, nullable)
  - `Agent` 新增 `custom_args` (JSONB, default=[])
  - 新增 migration 脚本

- [x] **T1.4** 修改 `backend/app/services/agent_runner.py`
  - `build_adapter_input()`: 按 `CLI_ADAPTERS` vs `SDK_ADAPTERS` 分支
    - CLI: 跳过 API key 解析、历史构建、工具引导、custom_config
    - CLI: 构建 `extra_env`（仅当 agent.api_key 显式设置）
    - SDK: 保持现有逻辑不变
  - `execute_simple_run()`: 工具注入只对 `SDK_ADAPTERS` 执行
  - 测试：`test_agent_runner.py` 覆盖 CLI/SDK 分支

## Phase 2: Claude Code CLI 适配器

- [x] **T2.1** 重写 `backend/app/adapters/claude_adapter.py`
  - 删除现有 SDK 实现（~400 行）
  - 实现 `ClaudeCLIAdapter(CLIAdapterBase)`
  - `_build_args()`: 构建 CLI 参数（参考 multica `buildClaudeArgs`）
  - `_write_prompt()`: 写 stream-json 格式 prompt
  - `_read_events()`: 解析 stdout JSONL → 翻译成 StreamEvent
    - system → status
    - assistant → text/thinking/tool_use
    - user → tool_result
    - result → usage + output
    - control_request → auto-respond allow
  - `claude_blocked_args`: 定义不可覆盖的参数
  - 跨平台：`hide_window` flag for Windows

- [ ] **T2.2** 测试 `backend/tests/adapters/test_claude_adapter.py`
  - Mock 子进程输出 → 验证 StreamEvent 翻译正确
  - 测试取消处理
  - 测试超时处理
  - 测试 session resume / fresh fallback
  - 测试 custom_args 过滤

## Phase 3: Codex CLI 适配器

- [x] **T3.1** 新增 `backend/app/adapters/codex_adapter.py`
  - 实现 `CodexCLIAdapter(CLIAdapterBase)`
  - `_build_args()`: `app-server --listen stdio://`
  - `_write_prompt()`: JSON-RPC initialize → thread/start → turn/start
  - `_read_events()`: JSON-RPC 通知 → StreamEvent
    - item/added → text/tool_call/thinking
    - turn/completed → usage + output
  - 超时监控：semantic_inactivity_timeout + first_turn_no_progress_timeout
  - `codex_blocked_args`: 定义不可覆盖的参数

- [ ] **T3.2** 测试 `backend/tests/adapters/test_codex_adapter.py`
  - Mock JSON-RPC 通信 → 验证事件翻译
  - 测试 resume thread / fallback to fresh thread
  - 测试 semantic inactivity timeout
  - 测试 first turn no progress timeout

## Phase 4: 注册 & 集成验证

- [x] **T4.1** 修改 `backend/app/adapters/registry.py`
  - 删除 `ClaudeAdapter()` 导入（SDK版）
  - 注册 `ClaudeCLIAdapter()` 为 `claude-code`
  - 注册 `CodexCLIAdapter()` 为 `codex`
  - 保留 `CustomAdapter()` 和 `MockAdapter()`

- [ ] **T4.2** 端到端验证
  - 创建 `claude-code` agent → 发送消息 → 验证 CLI 启动且返回正确事件
  - 创建 `codex` agent → 发送消息 → 验证 JSON-RPC 通信
  - 创建 `custom` agent → 发送消息 → 验证行为不变（regression check）
  - 取消 mid-run → 验证进程清理干净

- [x] **T4.3** 更新 `specs/05-adapter-interface.md`
  - ClaudeCodeAdapter 节：SDK→CLI 描述更新
  - CodexAdapter 节：SDK→CLI 描述更新
  - 新增「CLI 适配器公共行为」节
  - 现状说明表格更新
  - API key 解析节：标注 CLI agent 走 CLI 自带认证

## 各 Phase 依赖

```
Phase 1 (基础设施)
    │
    ├──▶ Phase 2 (Claude CLI) ──▶ Phase 4 (注册 & 验证)
    │                                │
    └──▶ Phase 3 (Codex CLI) ────────┘

Phase 2 和 Phase 3 可以并行开发
```

## 预估工作量

| Phase | 文件 | 预估代码量 |
|---|---|---|
| Phase 1 | cli_base.py + base.py + models.py + agent_runner.py | ~400 行 + tests |
| Phase 2 | claude_adapter.py（重写） | ~400 行 + tests |
| Phase 3 | codex_adapter.py（新建） | ~500 行 + tests |
| Phase 4 | registry.py + spec doc | ~30 行 + doc |
| **总计** | | **~1300 行 + tests** |
