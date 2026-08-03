# Tasks: enhance-claude-cli-adapter

## 1. Spike — control_request 格式探测

- [x] 1.1 修改 `claude_adapter.py` `_read_events`，当 `msg.type == "control_request"` 时，将完整的 `raw` dict 通过 `logger.info` 打印（JSON 格式，包含所有字段）
- [x] 1.2 将 `--permission-mode` 临时改为 `acceptEdits`，用一个简单的 prompt（如 "list files in current directory"）触发 Claude Code 调用工具
- [x] 1.3 记录 `control_request` 的实际字段结构：确认 tool name 字段名（`tool` vs `toolName`）、input 字段名、command 字段名、path 字段名
- [x] 1.4 如果 `control_request` 未按预期出现，检查是否需要 `--allowedTools` 或其他配置来触发审批事件；记录发现
- [x] 1.5 将 spike 发现写入 `design.md` 的 Open Questions 解答，恢复 `bypassPermissions`（待 Phase 3 实现后再切换）

## 2. Session Resume — DB 列 + 内存缓存层

- [x] 2.1 修改 `db/models.py`：给 `AgentRun` 模型新增 `cli_session_id: Mapped[str | None]` 列（nullable, 无默认值）
- [x] 2.2 创建 DB migration 脚本：`ALTER TABLE agent_runs ADD COLUMN cli_session_id TEXT`（SQLite + PostgreSQL 双引擎兼容）
- [x] 2.3 修改 `session_store.py`：将 `claude_code_sessions` 的 key 从 `conversation_id` 改为 `conversation_id:agent_id`（使用 `adapter_session_key` 函数），作为 DB 查询的缓存层
- [x] 2.4 新增 `get_claude_code_session(conversation_id: str, agent_id: str) -> str | None`：先查内存缓存，miss 时查 DB（`SELECT cli_session_id FROM agent_runs WHERE conversation_id=? AND agent_id=? AND cli_session_id IS NOT NULL ORDER BY started_at DESC LIMIT 1`），命中后回填缓存
- [x] 2.5 修改 `clear_claude_code_session` 签名为 `clear_claude_code_session(conversation_id: str) -> None`（清除该 conversation 下所有 agent 的内存缓存，DB 列不动）
- [x] 2.6 更新 `conversation_service.py` 中所有 `clear_claude_code_session` 调用（确认签名兼容）
- [x] 2.7 修改 `agent_runner.py` `build_adapter_input`：调用 `get_claude_code_session(args.conversation_id, args.agent_id)` 获取 session_id，赋给 `cli_resume_session_id`（替换当前的 `None` 硬编码）
- [x] 2.8 修改 `claude_adapter.py` `_read_events`：在 `result` 事件中捕获 `session_id`，通过新增的 `RunUsageEvent.session_id` 字段传出
- [x] 2.9 修改 `schemas/events.py`：给 `RunUsageEvent`（或 `RunEndEvent`）新增可选 `session_id: str | None` 字段
- [x] 2.10 修改 `agent_runner.py` `consume_stream`：在处理 `run.usage` 事件时，提取 `session_id` 并同时写入内存缓存（`claude_code_sessions[key] = session_id`）和 DB（`UPDATE agent_runs SET cli_session_id=? WHERE id=?`）
- [x] 2.11 实现 resume 失败降级：如果 `--resume` 后 result event 的 `session_id` 与传入的不同，或 run 报错，清除内存缓存并不写入 DB `cli_session_id`（保持 NULL），下次 run 自动用最新 DB 值重试
- [x] 2.12 确保 `specs/08-db-schema.md` 同步更新 `AgentRun` 新增 `cli_session_id` 列的说明

## 3. 智能审批 — control_request 路由

- [x] 3.1 修改 `claude_adapter.py` `_build_args`：将 `--permission-mode bypassPermissions` 改为 `--permission-mode acceptEdits`
- [x] 3.2 删除 `_auto_approve` 方法，新增 `_handle_control_request` 方法，接受 `proc`、`msg`、`input` 参数
- [x] 3.3 在 `_handle_control_request` 中解析 `control_request` 的 `request` dict，提取 tool name 和 input（字段名基于 Phase 1 spike 结果）
- [x] 3.4 实现 Bash 路由：调用 `find_banned_pattern(command, _PLATFORM)` → 命中则 deny；否则 `classify_bash_approval` → 需要审批则 `wait_for_bash_approval`；通过则 allow
- [x] 3.5 实现 Write/Edit 路由：调用 `resolve_safe_path(workspace, path)` → 路径越界则 deny；否则按 `fs_write_approval_mode` 决定：review 模式走 `pending_writes.register` + `await_pending_decision`，trust 模式直接 allow
- [x] 3.6 实现 `_allow` 和 `_deny` 辅助方法，构造 `control_response` JSON 并写入 stdin
- [x] 3.7 在 `_read_events` 主循环中将 `control_request` 分支从 `await self._auto_approve(proc, msg)` 改为 `await self._handle_control_request(proc, msg, input)`
- [x] 3.8 确保审批等待期间 `cancel_event` 可中断（`await_pending_decision` 已支持 `cancel_event`）
- [x] 3.9 添加 `--disallowedTools` 到 `_claude_blocked_args`（防止用户通过 custom_args 清空禁用列表）

## 4. 附件支持

- [x] 4.1 修改 `claude_adapter.py` `_write_prompt`：在构造 user message content 时，遍历 `input.attachments`
- [x] 4.2 对 `kind == "image"` 的附件：读取 `abs_path`，base64 编码，构造 `{"type": "image", "source": {"type": "base64", "media_type": attachment.mime_type, "data": "<base64>"}}` content block
- [x] 4.3 对 `kind == "file"` 的附件：在 prompt 文本末尾追加 `"\n\n[Attached file: <fileName> (<mimeType>) at <absPath>]"`
- [x] 4.4 添加图片大小上限检查（10 MB），超限抛出明确错误
- [x] 4.5 验证 Claude Code stream-json 是否接受 `image` content block 格式（如不接受，调整为文档要求的格式）

## 5. 动态 MCP 工具集

- [x] 5.1 修改 `claude_adapter.py` `_write_mcp_config`：新增 `tool_names` 参数，写入 MCP config 的 args 中（`--tool-names <comma-separated>`）
- [x] 5.2 修改 `claude_adapter.py` `_build_args`：将 `input.tool_names` 传给 `_write_mcp_config`
- [x] 5.3 修改 `mcp_bridge.py` `_parse_args`：新增 `--tool-names` 参数（默认空字符串）
- [x] 5.4 修改 `mcp_bridge.py` `main`：解析 `--tool-names`，非空时按逗号分割为 set，过滤 `tool_registry` 查询；为空时回退到 `CLI_MCP_TOOL_NAMES`
- [x] 5.5 将 `ACHAT_MCP_TOOL_HINT` 从硬编码常量改为函数 `_build_mcp_tool_hint(tool_names: list[str]) -> str`，动态生成工具列表
- [x] 5.6 在 `_build_args` 中调用 `_build_mcp_tool_hint(input.tool_names)` 替换硬编码常量

## 6. 超时看门狗

- [x] 6.1 在 `claude_adapter.py` 顶部新增常量：`DEFAULT_SEMANTIC_INACTIVITY_TIMEOUT = 10 * 60`、`DEFAULT_FIRST_TURN_NO_PROGRESS_TIMEOUT = 30`
- [x] 6.2 修改 `_read_events` 主循环：将 `async for line_raw in _read_lines(...)` 改为 `asyncio.wait_for` + 5 秒超时轮询模式
- [x] 6.3 新增 `last_semantic_activity` 时间戳，每次收到有意义的事件时更新
- [x] 6.4 新增 `first_turn_started` / `first_turn_progress` 标志，首次收到 assistant/text 事件时标记 progress=True
- [x] 6.5 超时触发时：标记 `final_status = "timeout"`，break 主循环，进入正常的 message.end + run.usage 发送流程

## 7. 清理 & 修复

- [x] 7.1 删除 `claude_adapter.py` 中未使用的 `output_parts` 变量及其所有 append 调用（第 233/356/462 行）
- [x] 7.2 修正 `DEFAULT_CLAUDE_MODEL` 从 `"claude-opus-4-8"` 改为 `"claude-opus-4-7"`（与 `model_registry.py` 一致），或改为 `None` 让 CLI 使用自己的默认模型

## 8. 测试

- [x] 8.1 编写 `test_claude_adapter.py`：Mock 子进程 stdout，验证 stream_event → StreamEvent 翻译正确
- [x] 8.2 测试 control_request 拦截：Mock bash control_request，验证黑名单命令被 deny
- [x] 8.3 测试 control_request 拦截：Mock write control_request，验证路径越界被 deny
- [x] 8.4 测试 session resume：验证首次 run 存储 session_id，二次 run 传入 `--resume`
- [x] 8.5 测试 session resume 失败降级：验证 resume 失败后清除 session 并重试
- [x] 8.6 测试附件支持：验证 image attachment 生成正确的 content block
- [x] 8.7 测试超时：模拟无输出场景，验证 timeout 状态

## 9. Spec 归档

- [x] 9.1 确认 `migrate-claude-codex-to-cli` change 已归档（或先归档它）
- [x] 9.2 归档 `enhance-claude-cli-adapter` change：将 delta spec 同步到 `openspec/specs/adapters/spec.md`
- [x] 9.3 更新 `specs/05-adapter-interface.md`：ClaudeCLIAdapter 节更新 permission mode、session resume、附件、动态 MCP 工具描述
