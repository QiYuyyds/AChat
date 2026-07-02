# Spec 05 — AgentPlatformAdapter 接口

> 适配器层屏蔽不同 Agent 平台（Claude Code、Codex、自配置 Agent）的 API 差异，对上层提供统一的事件流。**修改接口需先讨论。**

源文件：Python `backend/app/adapters/`（原 TypeScript 版 `src/server/adapters/`）

---

## 现状说明（先读这段）

| Adapter | 路线 | 状态 |
|---|---|---|
| `MockAdapter` | 预设脚本 | ✅ 已实现，用于开发期不烧 token |
| `CustomAgentAdapter` | **SDK/API** | ✅ 已实现，覆盖 DeepSeek / OpenAI / 火山方舟 / per-agent OpenAI-compatible Base URL；自写 tool loop |
| `ClaudeCLIAdapter` | **CLI 子进程** | ✅ 已实现，`spawn claude -p --output-format stream-json`，CLI 自带工具与审批 |
| `CodexCLIAdapter` | **CLI 子进程** | ✅ 已实现，`spawn codex app-server --listen stdio://`，JSON-RPC 2.0 通信 |

**路线说明**：2026-07 将 Claude Code 和 Codex 从 SDK/API 路线迁移到 CLI 子进程路线（参考 multica 设计）。Custom adapter 保持 SDK 路线不变。

---

## 定位

```
应用层 (AgentRunner)
       │
       │ stream(input, cancel_event) → AsyncIterator<StreamEvent>
       ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐
│ ClaudeCLI    │  │ CodexCLI     │  │ CustomAgent      │  │ Mock         │
│ Adapter      │  │ Adapter      │  │ Adapter          │  │ Adapter      │
│ (CLI 路线)   │  │ (CLI 路线)   │  │ (SDK 路线)       │  │ (mock)       │
└──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  └──────┬───────┘
       │                 │                   │                    │
  claude CLI        codex CLI           OpenAI SDK            预设脚本
  (子进程)          (子进程)            (Chat Completions)
  stream-json       JSON-RPC 2.0       + AChat tool loop
  自带工具          自带工具             AChat 管理工具
```

**Adapter 的唯一职责**：把厂商输出（SDK streaming response / CLI stdout 事件）翻译成 Spec 02 定义的 `StreamEvent`。

**Adapter 不做的事**：
- 不写数据库
- 不发 SSE
- CLI 适配器不执行工具（工具由 CLI 内部执行）；SDK 适配器可调 `toolRegistry`

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
  // Claude Code / Codex adapter 忽略此字段（用 SDK 内置工具集）
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
  // - ClaudeCodeAdapter / CodexAdapter：忽略（走 SDK 自己的 session resume）
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
- `systemPrompt` / `apiKey` 提升到根字段（不再嵌 `customConfig`）：所有 adapter 都需要，ClaudeCodeAdapter 也读这两个
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
| `ClaudeCodeAdapter` | `SDKResultMessage.usage`（success / error 都有）+ `modelUsage` 拿实际模型 id |
| `CodexAdapter` | `runStreamed()` 的 `turn.completed.usage` |
| `CustomAgentAdapter` | 调用时设 `stream_options: { include_usage: true }`，stream 末尾会有一个携 `usage` 的特殊 chunk；跨 turn 累加（一个 run 内可能 ≤ MAX_TURNS=8 次 chat.completions.create） |
| `MockAdapter` | 不上报 usage（agent_runs.usage = null） |

**字段映射**（OpenAI 协议 → 我们的 `RunUsage`）：
- `prompt_tokens` → `inputTokens`
- `completion_tokens` → `outputTokens`
- `prompt_cache_hit_tokens` (DeepSeek) / `cached_tokens` (OpenAI) → `cacheReadTokens`
- DeepSeek 不报 cache_creation；保持 0

**字段映射**（Anthropic SDK → 我们的 `RunUsage`）：
- `input_tokens` → `inputTokens`
- `output_tokens` → `outputTokens`
- `cache_creation_input_tokens` → `cacheCreationTokens`
- `cache_read_input_tokens` → `cacheReadTokens`

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

## API key 解析

**CLI 适配器**（claude-code, codex）使用厂商 CLI 自带的认证（`claude login` / `codex login` / 环境变量）。AChat 不参与 API key 解析。仅当 `agent.api_key` 显式设置时，注入为子进程环境变量（`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`）。

**SDK 适配器**（custom）走四层 key 解析链，由 `AgentRunner.build_adapter_input` 执行：

```
1. agents.api_key                   per-agent override（最高优先级）
2. app_settings.<provider>          用户在「设置」面板自填
3. process.env.<PROVIDER>_API_KEY   .env 兜底（dev / CI）
```

**Provider 字段映射**（Custom adapter）：

| agent.adapterName | 路线 | agent.modelProvider | API key 来源 |
|---|---|---|---|
| `claude-code` | CLI | — | CLI 自带认证。若 `agent.api_key` 设置，注入 `ANTHROPIC_API_KEY` 环境变量 |
| `codex` | CLI | — | CLI 自带认证。若 `agent.api_key` 设置，注入 `OPENAI_API_KEY` 环境变量 |
| `custom` | SDK | `anthropic` | `agent.api_key` → `app_settings.anthropicApiKey` → `ANTHROPIC_API_KEY` |
| `custom` | SDK | `openai` | `agent.api_key` → `app_settings.openaiApiKey` → `OPENAI_API_KEY` |
| `custom` | SDK | `deepseek` | `agent.api_key` → `app_settings.deepseekApiKey` → `DEEPSEEK_API_KEY` |
| `custom` | SDK | `volcano-ark` | `agent.api_key` → `app_settings.arkApiKey` → `ARK_API_KEY` |
| `custom` | SDK | `openai-compatible` | `agent.api_key`（per-agent only） |

**`apiBaseUrl` 按 adapter 分协议**：
- CLI adapters：不解析 `apiBaseUrl`。若 `agent.api_base_url` 设置，注入为环境变量（`ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL`）
- Custom adapter：命名 provider 使用 adapter 内置默认 base URL；`openai-compatible` 必须使用 per-agent `apiBaseUrl`

---

## ClaudeCLIAdapter

源文件：Python `backend/app/adapters/claude_adapter.py`

CLI 子进程路线：spawn `claude` CLI，通过 stream-json 协议（stdin/stdout）通信。CLI 内部管理自己的工具执行、沙箱、权限审批。Adapter 只负责翻译 CLI 事件流。

```python
class ClaudeCLIAdapter(CLIAdapterBase):
    name = "claude-code"  # AdapterName

    async def stream(self, input, cancel_event) -> AsyncIterator[StreamEvent]:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",
            stdin=PIPE, stdout=PIPE, stderr=PIPE,
            cwd=input.workspace_path,
            env=build_child_env(input.extra_env),
        )
        # write prompt as JSON to stdin
        # read JSONL from stdout, translate each line to StreamEvent
        # on cancel_event: graceful shutdown (close stdin → wait → terminate → kill)
```

### CLI 参数

| 参数 | 来源 | 说明 |
|---|---|---|
| `-p` | 硬编码 | 非交互模式 |
| `--output-format stream-json` | 硬编码 | 协议契约 |
| `--input-format stream-json` | 硬编码 | 协议契约 |
| `--permission-mode bypassPermissions` | 硬编码 | CLI 自主审批（daemon 模式） |
| `--verbose` | 硬编码 | 开启详细日志 |
| `--model <id>` | `AdapterInput.model_id` | 可空，CLI 用默认模型 |
| `--resume <id>` | `AdapterInput.resume_session_id` | 可空，恢复历史会话 |
| `--mcp-config <path>` | `AdapterInput.mcp_config` | 写入临时文件后传路径 |
| `custom_args` | `Agent.custom_args`（过滤后） | 用户自定义参数 |

Blocked args（用户 custom_args 中被过滤的）：`-p`, `--output-format`, `--input-format`, `--permission-mode`, `--mcp-config`, `--effort`

### 事件翻译

| CLI 事件 | 对应 StreamEvent |
|---|---|
| `system` / 首条消息 | `message.start` |
| `assistant` → content block `text` | `part.start({type:'text'})` + `part.delta({type:'text.append'})` |
| `assistant` → content block `thinking` | `part.start({type:'thinking'})` + `part.delta({type:'thinking.append'})` |
| `assistant` → content block `tool_use` | `tool.call({callId, toolName, args})` |
| `user` → content block `tool_result` | `tool.result({callId, result, isError})` |
| `result` | 记录 usage + output，跳出循环 |
| `control_request` | 自动 respond `{behavior: "allow"}` |
| `log` | 忽略（MVP） |

### 不做 / 推迟

- MCP server 配置 UI
- 审批桥（CLI 在 `bypassPermissions` 下自主审批）
- Subagent 独立 child run

---

## CodexCLIAdapter

源文件：Python `backend/app/adapters/codex_adapter.py`

CLI 子进程路线：spawn `codex app-server --listen stdio://`，通过 JSON-RPC 2.0 协议（stdin/stdout）通信。Codex CLI 内部管理工具执行、沙箱、线程生命周期。

```python
class CodexCLIAdapter(CLIAdapterBase):
    name = "codex"  # AdapterName

    async def _write_prompt(self, proc, input):
        # 1. JSON-RPC initialize → initialized
        # 2. thread/start (or thread/resume if resume_session_id set)
        # 3. turn/start with prompt

    async def _read_events(self, proc, input, cancel_event):
        # Read JSON-RPC notifications and translate:
        #   item/added → text/thinking/tool_call events
        #   turn/completed → usage + end
        #   turn/error → failed
```

### JSON-RPC 流程

```
1. request("initialize", {clientInfo, capabilities})
   notify("initialized")
2. request("thread/start" | "thread/resume", {threadId?, cwd, model, developerInstructions})
3. request("turn/start", {threadId, input: [{type:"text", text:"<prompt>"}]})
4. wait for notifications:
   notification("item/added")     → text / thinking / tool_call / tool_result
   notification("turn/completed") → usage + end
   notification("turn/error")     → failed
```

### 超时监控

- `semantic_inactivity_timeout`: 默认 10 分钟无语义活动 → timeout
- `first_turn_no_progress_timeout`: 默认 30 秒首次 turn 无进展 → timeout

### 不做 / 推迟

- MCP config TOML 渲染（multica 有约 700 行 TOML 生成逻辑，AChat MVP 跳过）
- 自定义 runtime profile
- Codex thread 缓存复用

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

当前注册的 adapter：`mock`、`custom`、`claude-code`、`codex`。

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
