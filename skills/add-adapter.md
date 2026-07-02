# Skill：新增一个 Adapter

> **目的**：接入一个新的 agent 平台（如 OpenCode、Hermes、OpenClaw、Gemini CLI），让它能像 Claude Code / Codex 一样被 Agent 选用。
> **契约文档**：`specs/05-adapter-interface.md`。本指南是它的「落地配方」。
> **基线**：Python 后端，CLI 子进程路线。参考 `backend/app/adapters/`。

---

## 何时用 / 何时不用

- ✅ 要接一个**真正的 agent 平台**（自带 agentic 循环、工具、session 的 CLI）——走 **CLI 子进程路线**。
- ❌ 只是想换一个**模型 provider**（DeepSeek / OpenAI / 火山方舟 等 OpenAI 兼容端点）——那走 `CustomAdapter` 配置即可，**不要**新建 adapter。判断标准：对方是「会自己调工具、写文件的 agent CLI」还是「一个 chat completions 端点」。

---

## 前置阅读

1. `specs/05-adapter-interface.md` —— 接口契约、事件翻译职责、取消约定。
2. `backend/app/adapters/base.py` —— `AgentPlatformAdapter` ABC + `AdapterInput`（**以此为准**）。
3. `backend/app/adapters/cli_base.py` —— CLI 适配器公共基类（子进程生命周期、管道、超时/取消、参数过滤）。
4. `backend/app/adapters/codex_adapter.py` —— 最贴近的 CLI 参考（JSON-RPC 2.0 通信 + 事件翻译）。
5. `backend/app/adapters/claude_adapter.py` —— stream-json 协议参考。

---

## 你要满足的契约

整个接口只有 **2 个成员**（`backend/app/adapters/base.py`）：

```python
class AgentPlatformAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> AdapterName: ...

    @abstractmethod
    def stream(
        self, input: AdapterInput, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]: ...
```

`stream` 是个 async generator，**只管把平台的输出翻译成 `StreamEvent` 并 `yield`**。它的事件生命周期必须是：

```
message.start
  → (part.start → part.delta* → part.end)*      // 文本 / thinking / code
  → (tool.call → tool.result)*                   // 工具调用
  → [run.usage]                                  // 可选,上报 token
message.end
```

> `run.start` / `run.end` **不归 adapter**，由 `AgentRunner` 在 adapter 外发。adapter 只发 `message.*` / `part.*` / `tool.*` / `run.usage` / `artifact.create`。

**铁律（CLAUDE.md §3.1）**：adapter **永不写 DB、永不推 SSE、不跨调用持有状态**。它只翻译事件；「事件 → 持久化 + 广播」唯一归 `AgentRunner`。

`AdapterInput` 里 CLI adapter 能读到的关键字段（`base.py`）：`prompt`（已拼好的完整提示）、`system_prompt`、`workspace_path`、`executable_path`（CLI 二进制路径）、`extra_env`（额外环境变量）、`custom_args`（CLI 自定义参数）、`resume_session_id`（跨 run 会话续接）、`mcp_config`（MCP server 配置）。CLI adapter 通常**不消费** `api_key` / `tool_names` / `history`（这些走 CLI 自带认证与工具）。

---

## 步骤

以新增 `OpenCodeAdapter` 为例（CLI 子进程路线）。

### 1. 扩展 `AdapterName` 联合

`backend/app/adapters/base.py`：

```python
AdapterName = Literal["mock", "custom", "claude-code", "codex", "opencode"]
```

> 新增一个全新名字（如 `'opencode'`）才需要在这里加。已有的 `'claude-code'` / `'codex'` 不用动。

### 2. 新建 adapter 文件，继承 `CLIAdapterBase`

新建 `backend/app/adapters/opencode_adapter.py`：

```python
from app.adapters.base import AdapterInput, AdapterName
from app.adapters.cli_base import BlockedArgMode, CLIAdapterBase, filter_custom_args


class OpenCodeAdapter(CLIAdapterBase):
    """Spawn ``opencode`` CLI, translate events into StreamEvent."""

    def __init__(self, executable_path: str = "opencode") -> None:
        super().__init__(executable_path=executable_path)

    @property
    def name(self) -> AdapterName:
        return "opencode"

    def _build_args(self, input: AdapterInput) -> list[str]:
        # 协议关键 flag 硬编码；custom_args 经 filter_custom_args 过滤后追加
        ...

    async def stream(self, input, cancel_event):
        # CLIAdapterBase 管子进程生命周期；你只写「读 stdout → 翻译 StreamEvent」循环
        ...
```

抄 `codex_adapter.py` 的整体结构：`_build_args()` 构建参数、`_write_prompt()` 写协议格式、`_read_events()` 逐行/逐消息解析翻译。定义 `_blocked_args` 防止用户覆盖协议关键 flag。

### 3. 在 registry 注册

`backend/app/adapters/registry.py`：

```python
from app.adapters.opencode_adapter import OpenCodeAdapter


def _build_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(MockAdapter())
    reg.register(CustomAdapter())
    reg.register(ClaudeCLIAdapter(...))
    reg.register(CodexCLIAdapter(...))
    reg.register(OpenCodeAdapter(
        executable_path=agent_executable_path_fallback("opencode", "opencode"),
    ))
    return reg
```

### 4. 放开创建入口的校验（否则 API 拒绝）

`backend/app/api/agents.py` 的创建校验只认已注册的 adapter name。新增 `'opencode'` 后同步：
- 创建/更新 Agent 的 Pydantic 校验 enum
- `backend/app/services/agent_runner.py` 的 `CLI_ADAPTERS` 集合（把 `'opencode'` 加进去，使其走 CLI 分支：跳过 API key 解析、工具注入、历史构建）

### 5. MCP Bridge（CLI agent 需要）

若新 CLI agent 需要调用 AChat 平台工具（`report_task_result` / `write_artifact` / `ask_user` 等），通过 `backend/app/mcp_bridge.py` 暴露。`AdapterInput.mcp_config` 已支持传入 MCP server 配置，CLI adapter 写临时文件并传 `--mcp-config <path>`。参考 `claude_adapter.py` 的 MCP 配置写入逻辑。

### 6.（可选）UI 与内置 Agent

- `src/shared/agent-builder-config.ts` 的 `AgentBuilderAdapter` 联合——想在创建弹窗里能选新 adapter 才需要改。
- `backend/app/db/models.py` 的内置 agent seed——想随项目种一个内置 agent 才加。

### 7. 同步 spec

`specs/05-adapter-interface.md`：现状表、新 adapter 节、新增步骤清单改成「已实现」。

---

## 常见坑

1. **`cancel_event` 必须贯穿每一次长操作**（CLAUDE.md §4.4）。
   - 生成器里每步前查 `if cancel_event.is_set(): return`。
   - CLI 适配器：`cancel_event` 触发 → 关 stdin → 等 grace period → terminate → kill（`cli_base.py` 已封装）。
   - 中止时静默 `return`，由 AgentRunner 判 run 为 `aborted`。

2. **类型存在 ≠ 运行可用**：`AdapterName` 联合（有）、registry 注册（无）、API 校验（无）、UI（无）四处各自为政。少接一处就在那一层断。接新平台时把 §1–§6 逐条过一遍。

3. **CLI adapter 不消费 `api_key` / `tool_names`**：CLI 自带认证（OAuth / 环境变量）与工具集。`build_adapter_input` 对 `CLI_ADAPTERS` 跳过这些；仅当 agent 显式设了 `api_key` 时才注入 `extra_env`。

4. **工具执行不归 AChat 的 ToolExecutor**（CLI 路线）：CLI 内部自己执行工具。AChat 的 `ToolExecutor` 只对 SDK 路线（Custom）生效。CLI agent 调 AChat 平台工具走 MCP bridge。

5. **Windows 子进程**：CLI 子进程在 Windows 上需隐藏窗口（`conpty.py` / `hide_window` flag），且环境变量需继承完整 `os.environ`（曾因缺 `SYSTEMROOT` 崩溃）。

6. **MCP 工具名前缀**：部分 CLI（如 Claude）会自动给 MCP 工具加前缀（`mcp__achat-tools__`），需在 adapter 里剥离后才能匹配 AChat 工具名。参考 `claude_adapter.py` 的 `_strip_mcp_tool_prefix`。

---

## 提交自检（对齐 CLAUDE.md §6.5）

- [ ] `ruff check .` / `pytest` 过
- [ ] 新 adapter 的 `stream` 严格遵守 `message.start → … → message.end` 生命周期，每个事件带 `conversation_id` + `timestamp`
- [ ] `cancel_event` 贯穿，中止能静默退出且子进程清理干净
- [ ] adapter 没碰 DB / 没推 SSE
- [ ] `AdapterName` / registry / API 校验 / (UI) 四处一致，选它不会运行时抛错
- [ ] `specs/05` 已同步
