# Design: migrate-claude-codex-to-cli

## 三条路线共存

```
                        AgentRunner
                             │
               ┌─────────────┼─────────────┐
               ▼             ▼             ▼
        ┌────────────┐ ┌────────────┐ ┌────────────┐
        │  CLI 路线   │ │  SDK 路线   │ │  Mock 路线  │
        │            │ │            │ │            │
        │ claude-code│ │  custom    │ │   mock     │
        │   codex    │ │            │ │            │
        └─────┬──────┘ └─────┬──────┘ └─────┬──────┘
              │              │              │
    ┌─────────┴──────┐       │              │
    ▼                ▼       ▼              ▼
claude CLI     codex CLI  OpenAI SDK    预设脚本
(stream-json)  (JSON-RPC) (Chat API)
```

三条路线实现同一个 `AgentPlatformAdapter` 接口，上游 `AgentRunner` 不需要知道后端是 CLI 还是 SDK。

## CLI 适配器统一执行流程

参考 multica 的 `agent.Backend.Execute()` 模式，Python 版如下：

```
1. 构建 CLI 参数
   ├── 协议关键 flag（硬编码，不可被 custom_args 覆盖）
   ├── model / cwd / resume_session / mcp_config（来自 AdapterInput）
   └── custom_args（来自 Agent 模型，过滤掉 blocked args）

2. asyncio.create_subprocess_exec()
   ├── cwd = workspace_path
   ├── env = 继承 os.environ + 剥离内部标记 + 合并 extra_env
   └── stdin=PIPE, stdout=PIPE, stderr=PIPE

3. 写入 prompt（按协议格式）
   ├── Claude: JSON line {"type":"user","message":{...}}
   ├── Codex: JSON-RPC turn/start

4. 逐行读取 stdout，翻译为 StreamEvent
   ├── 每行是一个 JSON 事件
   ├── 按 event.type 分派处理
   └── yield PartStart / PartDelta / PartEnd / ToolCall / ToolResult / ...

5. 等待进程退出
   ├── 正常退出 → 发送 Result
   ├── cancel_event 触发 → 关 stdin → 等 grace period → terminate → kill
   └── 超时 → 同上，status="timeout"
```

## 公共基类 `cli_base.py`

```python
@dataclass
class CLIProcess:
    """封装一个正在运行的 CLI 子进程。"""
    proc: asyncio.subprocess.Process
    cancel_event: asyncio.Event
    grace_timeout: float = 10.0  # 优雅关闭等待秒数

    async def shutdown(self) -> None:
        """优雅关闭：关 stdin → 等 grace_timeout → terminate → kill"""
        ...

    @staticmethod
    def filter_custom_args(
        args: list[str], blocked: dict[str, BlockedArgMode]
    ) -> list[str]:
        """过滤用户自定义参数中的协议关键 flag。参考 multica filterCustomArgs。"""
        ...

class CLIAdapterBase(AgentPlatformAdapter):
    """CLI 适配器基类。子类实现 _build_args() 和 _translate_event()。"""

    @abstractmethod
    def _build_args(self, input: AdapterInput) -> list[str]: ...

    @abstractmethod
    async def _write_prompt(self, proc, input: AdapterInput) -> None: ...

    @abstractmethod
    async def _read_events(self, proc, input, cancel_event) -> AsyncIterator[StreamEvent]: ...

    async def stream(self, input, cancel_event) -> AsyncIterator[StreamEvent]:
        proc = await self._spawn(input, cancel_event)
        try:
            await self._write_prompt(proc, input)
            async for event in self._read_events(proc, input, cancel_event):
                yield event
        finally:
            await CLIProcess(proc, cancel_event).shutdown()
```

### 进程安全

**跨平台差异**（参考 multica `claude.go` / `codex.go`）：

| 操作 | POSIX | Windows |
|---|---|---|
| 启动 | `asyncio.create_subprocess_exec` | 同（asyncio 抽象） |
| 取消 | `proc.terminate()` (SIGTERM) → `proc.kill()` (SIGKILL) | `proc.terminate()` (TerminateProcess) → `proc.kill()` |
| 进程组 | `os.setpgrp()` + `os.killpg()` | `proc.terminate()` 已够（Windows 无进程组概念） |
| 隐藏窗口 | 无需（服务端） | `CREATE_NO_WINDOW` flag |
| stderr 管道 | 直接读 pipe | 同 |

**优雅关闭流程**：
```
cancel_event.set()
  │
  ▼
stdin.close()              # 通知 CLI 不再有新输入
  │
  ▼
await asyncio.sleep(grace) # 等 CLI 自己退出
  │
  ▼ (超时)
proc.terminate()           # SIGTERM / TerminateProcess
  │
  ▼
await asyncio.sleep(2)
  │
  ▼ (超时)
proc.kill()                # SIGKILL / TerminateProcess(强制)
```

**环境变量隔离**（参考 multica `isFilteredChildEnvKey`）：
- 剥离的内部标记：`CLAUDE_CODE_SESSION_ID`、`CLAUDE_CODE_SSE_PORT`、`CLAUDE_CODE_EXECPATH`、`CLAUDECODE` 等
- 保留用户配置：`CLAUDE_CODE_GIT_BASH_PATH`、`ANTHROPIC_API_KEY`、`OPENAI_API_KEY` 等

## AdapterInput 扩展

```python
@dataclass
class AdapterInput:
    # ... 现有字段全部保留 ...

    # ─── CLI agent 专用（新增）───
    executable_path: str | None = None
    """CLI 二进制路径。空则在 PATH 中查找。SDK agent 忽略。"""

    extra_env: dict[str, str] | None = None
    """额外环境变量（含 per-agent API key override）。合并到子进程 env。SDK agent 忽略。"""

    custom_args: list[str] | None = None
    """用户自定义 CLI 参数。会被 filter_custom_args 过滤掉 blocked flags。SDK agent 忽略。"""

    resume_session_id: str | None = None
    """跨 run 的 session/thread ID。CLI agent 启动时传 --resume / thread/resume。SDK agent 忽略。"""

    mcp_config: dict | None = None
    """MCP server 配置。CLI agent 写入临时文件后传 --mcp-config <path>。SDK agent 忽略。"""
```

## Agent 模型扩展

```python
# models.py
class Agent(Base):
    # ... 现有字段全部保留 ...

    executable_path: Mapped[str | None]     # CLI 路径（空 → PATH 查找）
    protocol_family: Mapped[str | None]    # 'claude' | 'codex' | None（None=非CLI agent）

# AdapterName 类型
AdapterName = Literal["mock", "custom", "claude-code", "codex"]
# "claude-code" → CLI（ClaudeCLIAdapter）
# "codex"       → CLI（CodexCLIAdapter）
# "custom"      → SDK（CustomAdapter，不变）
# "mock"        → Mock（MockAdapter，不变）
```

`protocol_family` 字段决定 CLI 适配器的参数构建和协议解析。`executable_path` 允许用户指定非标准路径（如 `/opt/claude-nightly/bin/claude`）。

## AgentRunner 分支逻辑

`build_adapter_input` 中：

```python
CLI_ADAPTERS = {"claude-code", "codex"}
SDK_ADAPTERS = {"custom"}  # mock 不算 SDK

# 1. API key：CLI agent 不查 AChat 全局设置
#    CLI 自带认证（claude login / codex login / env var）
#    仅当 agent.api_key 显式设置时，注入 extra_env
if agent.adapter_name in CLI_ADAPTERS:
    extra_env = {}
    if agent.api_key:
        extra_env["ANTHROPIC_API_KEY" if agent.adapter_name == "claude-code" else "OPENAI_API_KEY"] = agent.api_key
    effective_api_key = None  # 不往下查 settings
else:
    # 现有逻辑：四层 key 链
    ...

# 2. 历史构建：CLI agent 用自己的 session resume
if agent.adapter_name in CLI_ADAPTERS:
    history = []
    effective_prompt = await prefix_prompt_with_context_summary(...)
else:
    # 现有逻辑：build_history_for + token budget 计算
    ...

# 3. 工具引导：CLI agent 不注入 tool_guidance
#    SDK agent 照旧
```

`execute_simple_run` 中：

```python
# 工具注入：只对 SDK agent
if agent.adapter_name in SDK_ADAPTERS:
    # memory_recall 注入
    # load_skill 注入
    # RAG tools 注入
# CLI agent: 跳过所有工具注入。工具由 CLI 自带
```

## AChat MCP Bridge

CLI agent 自带全套工具（bash、fs_read、fs_write、grep、glob 等），但缺少 AChat 平台专属能力。通过 MCP Server 桥接：

```
┌──────────────────────────────────────────────┐
│           AChat MCP Bridge（stdio）            │
├──────────────────────────────────────────────┤
│  工具            实现                         │
│  ─────────────   ───────────────────────     │
│  write_artifact   调 tool_registry.execute()  │
│  read_artifact    同上                        │
│  deploy_artifact  同上                        │
│  deploy_workspace 同上                        │
│  ask_user         发布 AskUser 事件           │
│  report_task_...  调 tool_registry.execute()  │
│                                              │
│  生命周期：                                   │
│  • 每个 agent run 启动一个 MCP Server 子进程  │
│  • 配置写入临时文件，传给 CLI（--mcp-config）  │
│  • run 结束时随 CLI 一起清理                  │
└──────────────────────────────────────────────┘
```

MVP 阶段：AChat MCP Bridge 可推迟。CLI agent 先用自己的工具，产物通过 workspace 文件体现。

## Claude Code CLI 适配器

参考：multica `claude.go`（~880 行）→ Python 版约 400 行

```
协议：stream-json over stdin/stdout

启动参数（硬编码，不可覆盖）：
  claude -p
    --output-format stream-json
    --input-format stream-json
    --verbose
    --permission-mode bypassPermissions
    --strict-mcp-config
    [--model <model_id>]
    [--resume <session_id>]
    [--mcp-config <temp_file>]
    [--max-turns <n>]
    [--effort <level>]
    [custom_args...]          ← 用户自定义，已过滤 blocked args

Blocked args（用户 custom_args 中的这些会被去掉）：
  -p, --output-format, --input-format, --permission-mode, --mcp-config, --effort

stdin 输入格式：
  {"type":"user","message":{"role":"user","content":[{"type":"text","text":"<prompt>"}]}}\n

stdout 事件 → StreamEvent 映射：
  type: "system"             → Message{type: status, status: "running"}
  type: "assistant"          → text→PartStart/Delta, thinking→PartStart/Delta, tool_use→ToolCall
  type: "user"               → tool_result→ToolResult
  type: "result"             → 记录 usage + output，准备结束
  type: "control_request"    → 自动 respond allow（autonomous mode）
  type: "log"                → 忽略（仅日志）
```

## Codex CLI 适配器

参考：multica `codex.go`（~2162 行）→ Python 版约 500 行（精简版，因为 multica 有很多 MCP TOML 转换逻辑）

```
协议：JSON-RPC 2.0 over stdin/stdout

启动参数：
  codex app-server --listen stdio:// [custom_args...]

Blocked args：
  --listen

JSON-RPC 流程：
  1. request("initialize", {clientInfo, capabilities})
     notify("initialized")
  2. request("thread/start" | "thread/resume", {threadId?, cwd, model, developerInstructions})
  3. request("turn/start", {threadId, input: [{type:"text", text:"<prompt>"}]})
  4. 等通知：
     notification("item/added")     → 文本/tool_call/thinking
     notification("turn/completed") → 结束 + usage
  5. 超时监控：
     semantic_inactivity_timeout (default 10min)
     first_turn_no_progress_timeout (default 30s)
```

---

## 不做 / 推迟

- **MCP Bridge**：MVP 不实现，CLI agent 先纯用自带工具
- **自定义 runtime profile**（multica 的 runtime_profiles 表）：后续再加
- **Agent 级别的 custom_args UI**：MVP 手动写 JSON array
- **Codex 的 MCP config.toml 渲染**：multica 有大量 TOML 渲染逻辑（codex.go 约 700 行用于 MCP config），MVP 跳过，只用 CLI 参数传 MCP
- **其他厂商 CLI**（Copilot、Cursor、CodeBuddy...）：不在本期范围
- **Windows 兼容**：先实现 POSIX 路径，Windows 路径后续补充
