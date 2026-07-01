# Spec 05 — AgentPlatformAdapter 接口

> 适配器层屏蔽不同 Agent 平台（Codex CLI、自配置 Agent）的 API 差异，对上层提供统一的事件流。**修改接口需先讨论。**

源文件：`src/server/adapters/`

---

## 现状说明（先读这段）

| Adapter | 状态 |
|---|---|
| `MockAdapter` | ✅ 已实现，用于开发期不烧 token |
| `CustomAgentAdapter` | ✅ 已实现，覆盖 DeepSeek / OpenAI / 火山方舟 / per-agent OpenAI-compatible Base URL（OpenAI Chat Completions 兼容协议）；**Anthropic 路径在 buildClient 里直接 throw，待实装** |
| `CodexCLIAdapter` | ✅ 已实现，基于 `codex app-server --listen stdio://` 子进程 + JSON-RPC 2.0 通信；MCP bridge 暴露 AgentHub 工具；CODEX_HOME 隔离 |

---

## 定位

```
应用层 (AgentRunner)
       │
       │ stream(input, signal) → AsyncIterable<StreamEvent>
       ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐
│ CodexCLI         │  │ CustomAgent      │  │ Mock         │
│ Adapter          │  │ Adapter          │  │ Adapter      │
│ ✅ 已实现         │  │ ✅ 已实现         │  │ ✅ 已实现     │
└──────┬───────────┘  └────────┬─────────┘  └──────┬───────┘
       │                       │                    │
   codex app-server       OpenAI SDK            预设脚本
   (JSON-RPC stdio)       (DeepSeek / 火山方舟 / OpenAI /
   + MCP bridge           自定义 OpenAI-compatible
   (agenthub-codex-       均走 Chat Completions 兼容协议)
    mcp.mjs)              + 自写 tool loop
```

**Adapter 的唯一职责**：把厂商 SDK 的输出翻译成 Spec 02 定义的 `StreamEvent`。

**Adapter 不做的事**：
- 不写数据库
- 不发 SSE
- 不持有跨调用的状态（除厂商 SDK 的 client 实例）

**Adapter 现状放宽的事**（与 CLAUDE.md §3.1 铁律有张力）：
- **直接 import `toolRegistry` 自跑 tool loop** —— CustomAgentAdapter 模块顶部 `import { toolRegistry } from '@/server/tools/registry'`，loop 内 `toolRegistry.execute(name, args, ctx)`。设计上工具执行属 L3，但代码现状是 Adapter 自调。本 spec 承认放宽，原因见下方「工具执行」一节

---

## 接口定义

```typescript
interface AgentPlatformAdapter {
  readonly name: AdapterName

  stream(
    input: AdapterInput,
    signal: AbortSignal,
  ): AsyncIterable<StreamEvent>
}

interface AdapterInput {
  agentId: string               // 用于事件 tag
  conversationId: string
  runId: string
  parentRunId?: string          // Orchestrator 派出的子 run

  prompt: string                // 已被外层拼好的完整 prompt（群聊场景含 XML 包装，详见 Spec 06）
  workspacePath: string         // 该会话的 workspace 绝对路径

  // 系统提示，AgentRunner 已注入 <workspace_info> 块。所有 adapter 共用
  systemPrompt: string

  // 该 agent 单独的 API key；null 时 adapter 走环境 / OAuth fallback
  apiKey: string | null

  // 当前 agent 可用的工具名。Custom adapter 用 toolRegistry.resolve(toolNames) 拿 ToolDef；
  // Codex CLI adapter 忽略此字段（通过 MCP bridge 获取工具）
  toolNames: string[]

  // 附件（用户上传的文件/图片）— 用于 multimodal 投递
  attachments?: Array<{
    id: string                  // att_<nanoid>
    fileName: string
    mimeType: string
    kind: 'image' | 'file'
    absPath: string             // 服务端绝对路径，Adapter 自读
  }>

  // 跨 run 对话历史（OpenAI ChatMessage 格式），不含当前触发消息。
  // 由 AgentRunner 通过 conversation-context.buildHistoryFor 序列化，详见 Spec 13。
  // - CustomAgentAdapter：拼到 [system, ...history, currentUser] 中间
  // - CodexCLIAdapter：忽略（走 Codex app-server 自己的 session resume）
  // - MockAdapter：忽略
  history?: ChatCompletionMessageParam[]

  // 仅 CustomAgentAdapter 使用（OpenAI 兼容协议特有的模型选择）
  customConfig?: {
    modelProvider: 'anthropic' | 'openai' | 'deepseek' | 'volcano-ark' | 'openai-compatible'
    modelId: string
    supportsVision: boolean     // 决定是否把图片附件以 image_url block 投递给 LLM
  }
}
```

**变更点（与早期 spec 差异）**：
- `tools: ToolDef[]` → `toolNames: string[]`：避免把 handler 函数引用塞进 input；Adapter 用 `toolRegistry.resolve` 自查
- 新增 `attachments`：multimodal 路径
- 新增 `customConfig.modelProvider: 'volcano-ark'`：OpenAI-compat 接入
- 新增 `customConfig.modelProvider: 'openai-compatible'`：per-agent `apiBaseUrl` + `apiKey` 的通用 Chat Completions 兼容接入
- `systemPrompt` / `apiKey` 提升到根字段（不再嵌 `customConfig`）：所有 adapter 都需要
- 新增 `customConfig.apiKey`：per-agent API key（优先级高于 env，见 Spec 08）
- 新增 `customConfig.supportsVision`：决定是否把图片以 multimodal 投递
- 新增 `parentRunId`：Orchestrator 子 run 的父引用

---

## CustomAgentAdapter

源文件：`src/server/adapters/custom-agent-adapter.ts`

最复杂的 adapter：自己实现 tool loop，覆盖 DeepSeek / OpenAI / 火山方舟 / 通用 OpenAI-compatible provider（其中 Anthropic 仍 TODO）。

### 高层流程

```
1. buildClient(provider, apiKey, apiBaseUrl) → OpenAI 兼容 client
   apiKey 来自 AdapterInput.apiKey（已由 AgentRunner 按四层链解析，
   见下方顶级章节「API key 解析（共四层）」）。
   provider → baseURL 映射：
     deepseek    → https://api.deepseek.com/v1
     volcano-ark → https://ark.cn-beijing.volces.com/api/v3
     openai      → https://api.openai.com/v1
     openai-compatible → agent.apiBaseUrl（必填）
     anthropic   → throw (TODO)
   
2. 初始化 messages:
   [
     { role: 'system', content: customConfig.systemPrompt },
     { role: 'user',   content: buildMultimodalUserContent(prompt, attachments) },
   ]
   
   buildMultimodalUserContent: 
     - supportsVision=true 且有图片附件：用 OpenAI content blocks 数组
       [{ type: 'text', text }, { type: 'image_url', image_url: { url: 'data:<mime>;base64,...' } }, ...]
     - 否则纯文本字符串
   
3. tool loop（最多 MAX_TURNS=8 轮）:
   每轮：
     yield message.start (新 partIndex 重置)
     client.chat.completions.create({ model, messages, tools, stream: true })
     for await chunk:
       - delta.content → text part
       - delta.reasoning_content → thinking part (DeepSeek 思维链)
       - delta.tool_calls → 累积 tool_calls
     yield message.end
     
     若无 tool_calls：跳出循环
     若有 tool_calls：
       并行 toolRegistry.execute(name, args, ctx)
       逐个 yield tool.result
        write_artifact 检测 result.value.artifactId 存在 → yield artifact.create (拉 DB 详情)
        deploy_artifact / deploy_workspace 检测 DeployStatusRecord → yield deploy.status
       把 assistant 消息（含 tool_calls + reasoning_content）和 tool result 推回 messages
       继续下一轮
```

### Reasoning 内容（DeepSeek thinking mode）

DeepSeek 等支持思考链的模型在 stream 中会单独输出 `delta.reasoning_content`。Adapter 处理：

1. 第一次见到 reasoning_content 时 emit `part.start` (type='thinking', content='')
2. 后续累积到 `reasoningBuffer`，emit `part.delta` (type='thinking.append')
3. **关键**：assistant 消息推回 messages 时必须带上 `reasoning_content` 字段，否则 DeepSeek 会报：

> `400 The reasoning_content in the thinking mode must be passed back to the API.`

这是 DeepSeek 特殊协议要求；其它 provider 忽略此字段。

### Multimodal

`buildMultimodalUserContent`（`custom-agent-adapter.ts:337-358`）：

- agent `supportsVision=true` 且 attachments 含 `kind='image'`：把 prompt 包成 OpenAI content blocks 数组，图片走 `image_url` block（base64 data URI），mimeType 来自 attachment row
- agent `supportsVision=false` 或无图片：纯文本字符串（沿用 OpenAI legacy 单字符串 content）

DeepSeek 的多模态模型（`deepseek-v4-flash`）走标准 OpenAI image_url 协议。

### Artifact / Deploy 注入路径

不在 Adapter 自己发 `artifact_ref` / `deploy_status` part。流程：

1. Adapter 检测 `tool_result.value.artifactId` 非空 → `yield { type: 'artifact.create', artifact: <DB row> }`
2. AgentRunner 接到 `artifact.create` 事件 → 在当前 message 末尾插入 `artifact_ref` part 并补发 `part.start`
3. Adapter 检测 `DeployStatusRecord`（来自 `deploy_artifact` / `deploy_workspace`）→ `yield { type:'deploy.status', messageId, deployment }`
4. AgentRunner 接到 `deploy.status` → 插入 `deploy_status` part 并补发 `part.start`
5. 这样 message.parts 里 tool_use → tool_result → artifact_ref / deploy_status 顺序排列，前端按 callId 合并工具卡片，其它结构化 part 单独渲染为卡片

详见 Spec 02 的「artifact_ref / deploy_status 注入路径」一节。

---

## 工具执行：Adapter 与 Runner 的边界

源文件：`src/server/adapters/custom-agent-adapter.ts:47`

```typescript
import { toolRegistry } from '@/server/tools/registry'
// ...
const result = await toolRegistry.execute(name, args, ctx)
```

**为什么放宽 §3.1 铁律**：

| 方案 | 描述 | 取舍 |
|---|---|---|
| **A. Adapter 自调 toolRegistry**（现状） | 模块顶部 import，loop 中直接 execute | 代码简单；Adapter 多一个依赖 |
| **B. Adapter 只 yield 事件，Runner 执行后注入** | Adapter 必须支持「暂停-等待 result-继续」 | 干净；async iterator 双向通信复杂 |

---

## Token usage 采集

所有 adapter 在 run 结束前 yield 一次 `run.usage` 事件（见 Spec 02），AgentRunner 收到后写入 `agent_runs.usage` JSON 列（见 Spec 08）。

| Adapter | usage 来源 |
|---|---|
| `CodexCLIAdapter` | `turn.completed` 事件的 `usage` 字段 |
| `CustomAgentAdapter` | 调用时设 `stream_options: { include_usage: true }`，stream 末尾会有一个携 `usage` 的特殊 chunk；跨 turn 累加（一个 run 内可能 ≤ MAX_TURNS=8 次 chat.completions.create） |
| `MockAdapter` | 不上报 usage（agent_runs.usage = null） |

**字段映射**（OpenAI 协议 → 我们的 `RunUsage`）：
- `prompt_tokens` → `inputTokens`
- `completion_tokens` → `outputTokens`
- `prompt_cache_hit_tokens` (DeepSeek) / `cached_tokens` (OpenAI) → `cacheReadTokens`
- DeepSeek 不报 cache_creation；保持 0

`lastInputTokens` 取本次 run 的 input prompt 长度，UI 用作「当前 context 大小」仪表。`model` 字段记录实际使用模型，按模型聚合用。

仅记 token 数量，**不算成本**（不同 provider / 第三方网关价格差异大，价格表难维护准确）。Cache hit 数量本身就足够看出节约程度。


方案 A 已落地。本 spec 承认放宽 —— Adapter **可以**调用 `toolRegistry`，但仍不**直接写 DB / 发 SSE**。Runner 仍是唯一的「event → 持久化 + 广播」入口。

如果未来要重新隔离（比如要给 Adapter 跑在 worker thread / 子进程），再切方案 B。

---

## MockAdapter

源文件：`src/server/adapters/mock-adapter.ts`

```typescript
class MockAdapter implements AgentPlatformAdapter {
  readonly name = 'mock' as const

  async *stream(input, signal) {
    const script = this.scripts.get(input.agentId) ?? DEFAULT_MOCK_SCRIPT
    for (const event of script) {
      if (signal.aborted) return
      await sleep(50)
      yield event
    }
  }
}
```

**用途**：开发期不烧 token、单元测试、演示环境备份。

---

## API key 解析（共三层）

所有 adapter 走同一套 key 解析链，由 `AgentRunner.buildAdapterInput`（`src/server/agent-runner.ts`）执行。Adapter 只看 `AdapterInput.apiKey` 一个字段，不关心来源。

```
1. agents.api_key                   per-agent override（最高优先级）
2. app_settings.<provider>          用户在「设置」面板自填（Spec 08 §8）
3. process.env.<PROVIDER>_API_KEY   .env.local 兜底（dev / CI）
```

**Provider 字段映射**（用于第 2 / 3 层选具体字段）：

| agent.adapterName | agent.modelProvider | app_settings 字段 | env var |
|---|---|---|---|
| `codex` | — | `openaiApiKey` | `CODEX_API_KEY` → `OPENAI_API_KEY` |
| `custom` | `anthropic` | `anthropicApiKey` | `ANTHROPIC_API_KEY` |
| `custom` | `openai` | `openaiApiKey` | `OPENAI_API_KEY` |
| `custom` | `deepseek` | `deepseekApiKey` | `DEEPSEEK_API_KEY` |
| `custom` | `volcano-ark` | `arkApiKey` | `ARK_API_KEY` |
| `custom` | `openai-compatible` | —（per-agent only） | — |

**`apiBaseUrl` 按 adapter 分协议**：
- Codex CLI：不使用 `apiBaseUrl`。Codex CLI 通过 `CODEX_HOME` 隔离的 `config.toml` 管理认证，不读取用户本机 `~/.codex/config.toml`。认证文件 `auth.json` 从 `~/.codex/auth.json` symlink 共享。
- Custom：命名 provider 使用 adapter 内置默认 base URL；`openai-compatible` provider 必须使用 per-agent `apiBaseUrl`，该 URL 必须是 OpenAI Chat Completions 兼容 endpoint。

**优化点**：`buildAdapterInput` 只在 `agent.apiKey` 为空时才查 `app_settings`，避免每次构造 input 都打 DB。

**Adapter 视角**：
- **CodexCLIAdapter**：不接收 `apiKey` / `apiBaseUrl`。Codex CLI 自己管理认证（通过 `CODEX_HOME` 下的 `auth.json`）。`toolNames` 也不传递给 codex；AgentHub 工具通过 MCP bridge 暴露。
- **CustomAgentAdapter**：命名 provider 把 `input.apiKey` 传 `new OpenAI({ apiKey })` 或 provider 默认 `baseURL`；`openai-compatible` 传 `new OpenAI({ apiKey, baseURL: input.apiBaseUrl })`，缺 key/base URL 时在调用上游前抛清晰错误
- **MockAdapter**：忽略

**用户需要在本机安装 codex CLI 并完成认证**（`npm install -g @openai/codex` + `codex auth`）。

---

## CodexCLIAdapter

源文件：`backend/app/adapters/codex_cli_adapter.py`

封装 Codex CLI 的 `app-server` 模式：通过 `asyncio.create_subprocess_exec` 启动 `codex app-server --listen stdio://` 子进程，使用 JSON-RPC 2.0 协议通过 stdin/stdout 通信。不使用任何 npm SDK，直接与 Codex CLI 二进制交互。

### 可执行文件解析

优先级：`AdapterInput.executable_path` → 环境变量 `CODEX_EXECUTABLE` → PATH 搜索 `codex`。三者均失败时抛出清晰错误提示用户安装 codex CLI（`npm install -g @openai/codex`）。

### CODEX_HOME 隔离

每个 run 使用独立的 `CODEX_HOME` 目录（由 `codex_home.py` 的 `prepare_codex_home()` 创建）：

1. **auth.json symlink**：从 `~/.codex/auth.json` symlink 到 per-run 目录（共享认证，不拷贝）
2. **sessions/ symlink**：从 `~/.codex/sessions/` symlink（共享 session 续接）
3. **config.toml copy**：从 `~/.codex/config.toml` 拷贝（如不存在则创建空文件），并注入 `[mcp_servers.agenthub]` 块
4. **权限**：config.toml 设为 `0o600`（POSIX）
5. **清理**：run 结束后 `cleanup_codex_home()` 可选清理

### MCP bridge

通过 `config.toml` 中的 `[mcp_servers.agenthub]` 块启动 `scripts/agenthub-codex-mcp.mjs` stdio server。bridge 暴露以下工具（由 `AGENTHUB_ALLOWED_TOOLS` 环境变量控制）：

- `plan_tasks` / `write_artifact` / `read_artifact` / `read_attachment`
- `deploy_artifact` / `deploy_workspace` / `ask_user` / `report_task_result`
- `fs_list`

bridge 通过带 token 的内部 API（`POST /api/internal/agenthub-tools`）调用后端 `toolRegistry`，token 由 `internal_token.py` 的 `generate_tool_token()` 生成，每个 run 独立。

### JSON-RPC 通信

```python
# 请求
class _Request(TypedDict):
    jsonrpc: str  # "2.0"
    id: int
    method: str
    params: dict

# 发送 thread/start + thread/run
await self._send_request("thread/start", {"cwd": workspace_path})
await self._send_request("thread/run", {"prompt": prompt, ...})

# 逐行读取 stdout 事件
async for line in proc.stdout:
    event = json.loads(line)
    yield from self._translate_event(event)
```

### 事件翻译

| Codex JSON-RPC 事件 | 对应 StreamEvent |
|---|---|
| `thread.started` | 缓存 threadId |
| `agent_message` (delta) | `message.start` → `part.start`(text) → `part.delta`(text.append) → `part.end` → `message.end` |
| `reasoning` (delta) | `part.start`(thinking) → `part.delta`(thinking.append) → `part.end` |
| `command_execution` | `tool.call` + `tool.result`（命令执行结果） |
| `mcp_tool_call` | `tool.call` + `tool.result`（MCP bridge 工具调用） |
| `turn.completed` | `message.usage` + `run.usage`（提取 token 用量） |
| EOF / process exit | `message.end`（如未已发送） |

`message.start` / `message.end` 由 adapter 管理；`run.start` / `run.end` 由 AgentRunner 包外发。

### 取消逻辑

监听 `cancel_event`：
1. `proc.terminate()` — 发送 SIGTERM
2. 等待 5s
3. 若仍存活：`proc.kill()` — 发送 SIGKILL

### MCP 环境变量

Adapter 构造以下环境变量传给子进程：

| 环境变量 | 值 | 用途 |
|---|---|---|
| `CODEX_HOME` | per-run 目录 | 隔离 Codex 配置 |
| `AGENTHUB_INTERNAL_BASE_URL` | `http://localhost:8000/api` | MCP bridge 调用后端 |
| `AGENTHUB_INTERNAL_TOOL_TOKEN` | per-run token | 认证 |
| `AGENTHUB_CONVERSATION_ID` | conversation_id | 上下文 |
| `AGENTHUB_AGENT_ID` | agent_id | 上下文 |
| `AGENTHUB_RUN_ID` | run_id | 上下文 |
| `AGENTHUB_ALLOWED_TOOLS` | 逗号分隔的工具名 | 控制可用工具 |

---

## AgentRegistry：根据 Agent 路由到 Adapter

源文件：`src/server/adapters/registry.ts`

```typescript
class AgentRegistry {
  private adapters: Map<AdapterName, AgentPlatformAdapter>

  getAdapter(agent: Agent): AgentPlatformAdapter {
    const adapter = this.adapters.get(agent.adapterName)
    if (!adapter) throw new Error(`Unknown adapter: ${agent.adapterName}`)
    return adapter
  }
}
```

当前注册的 adapter：`mock`、`custom`、`codex`。

---

## 错误处理

- Adapter 内部捕获厂商 SDK 异常 → throw 出 stream；AgentRunner 接住后为该 run 内未配对 `tool_result` 的 `tool_use` 补发 `isError=true` 结果，写 `run.end({ status: 'failed', error })`，并通过 `emitErrorVisualisation` 注入一条 `msg_err_*` 错误消息让用户在对话里看到（见 Spec 09 / Spec 02）
- **网络/速率限制类错误的重试**：CustomAgentAdapter 通过 OpenAI SDK 的 `maxRetries=2`（在 `buildClient` 中显式声明，常量 `MAX_API_RETRIES`）自动重试，对 408 / 429 / >= 500 / `APIConnectionError` 走指数退避。注意：**重试只对初始连接生效**，stream 一旦开始 emit chunks 就不再重试。如果要按 provider 调整次数（比如火山方舟更宽松），改这个常量
- LLM 输出 JSON Schema 不符 / tool args 解析失败 → 由 `toolRegistry.execute` 内部 catch 成 `tool.result.isError=true`，**不**视作 Adapter 错误

---

## 新增 Adapter 的步骤

1. 在 `src/server/adapters/` 创建 `<name>-adapter.ts`
2. 实现 `AgentPlatformAdapter` 接口
3. 在 `src/shared/types.ts` 的 `AdapterName` 联合类型加新值
4. 在 `adapters/registry.ts` 注册
5. （UI 路径）在 `src/components/create-agent-dialog.tsx` 加新 provider 选项
6. （seed）若是内置 agent，在 `src/db/seed.ts` 加种子
7. 写至少 1 个单元测试覆盖事件翻译核心路径
8. 更新本 spec 的「现状说明」表格

---

## 与其它 spec 的关系

- Spec 01：Agent.adapterName 决定路由到哪个 Adapter
- Spec 02：StreamEvent 是 Adapter 的输出 schema
- Spec 06：Orchestrator 给子 agent 拼 prompt 时也走 CustomAgentAdapter（Orchestrator 本身也是一个 custom agent）
- Spec 07：toolNames 引用的工具定义
- Spec 08：agents.api_key / api_base_url 字段；app_settings 表（全局 key 兜底）
