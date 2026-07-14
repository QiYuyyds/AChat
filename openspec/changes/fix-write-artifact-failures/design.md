# Design

## Context

Custom（SDK）Agent 通过 `CustomAdapter` 调用 OpenAI 兼容 API，LLM 生成 `write_artifact` tool call 参数后由 `_run_react_loop` 执行。当前链路存在多层失败点：

1. `call_once` / `stream` 不设 `max_tokens`，provider 默认值（如 DeepSeek 4096）对大型产物不够
2. 输出截断后 `json.loads(args_buffer)` 失败，被 `except: args = {}` 静默吞掉
3. `build_artifact_content` 的 Mermaid 校验过于严格（拒绝带围栏、缺 declaration 的图）
4. 各 `_build_*` 函数的 key 别名不够全面
5. `write_artifact` 的错误消息对 LLM 无用（Pydantic ValidationError 原文）
6. 大型 web_app 产物无法在一次 tool call 内完成

相关文件：`custom_adapter.py`、`model_registry.py`、`mermaid_normalize.py`、`artifact_service.py`、`write_artifact.py`、`agent_runner.py`。

## Goals / Non-Goals

**Goals:**
- 消除因 `max_tokens` 未设置导致的 LLM 输出截断
- 截断发生时给 LLM 明确的错误指引而非无意义的空参数校验失败
- Mermaid 校验能自动修复 LLM 常见输出格式（围栏、缺失 declaration）
- `write_artifact` 的 content 格式容错覆盖 LLM 常见变体
- 错误消息附带期望格式示例，使 LLM 能自修复
- 大型 web_app 产物可分片写入

**Non-Goals:**
- 不修改 CLI Agent（Claude Code / Codex）路径的 MCP Bridge DB 问题（另行处理）
- 不修改 artifact 数据模型（不加新列、不改表结构）
- 不引入新的 artifact type
- 不修改前端渲染逻辑

## Decisions

### D1: max_tokens 动态推导（而非固定值）

**选择**：在 `call_once` / `stream` 中，从 `get_model_limits(provider, model_id)` 获取 `context_window`，减去估算的输入 token 数，得到 `max_tokens`。

```python
limits = get_model_limits(model_provider, model_id)
input_estimate = estimate_tokens(json.dumps(messages, ensure_ascii=False))
max_tokens = max(limits.output_reserve, limits.context_window - input_estimate - 512)
# 但不超过 provider 的硬上限（如有）
```

**理由**：`output_reserve`（默认 4096）是为 history budget 计算设计的下限，不代表 LLM 的最大输出能力。动态推导能在输入较短时给出更大的输出空间。

**替代方案**：直接调大 `DEFAULT_OUTPUT_RESERVE` 到 8192。否决：会影响 history budget 计算，且不同 provider 上限不同，一刀切不合理。

**约束**：部分 provider（如 DeepSeek reasoner）的 `max_tokens` 含 thinking tokens，需确保 `output_reserve` 对 reasoning 模型足够大（已有 `deepseek-reasoner: outputReserve=16384`）。

### D2: 截断检测 — finish_reason + JSON 完整性双判断

**选择**：在 `call_once` 和 `stream` 解析 tool_calls 时，当 `json.loads(args_buffer)` 失败，检查两个条件：
1. `finish_reason == "length"` — 明确的截断信号
2. `args_buffer` 不以 `}` 或 `]` 结尾 — JSON 不完整的启发式判断

满足任一条件时，不传空 `{}` 给工具，而是直接 emit `ToolResultEvent` 带截断错误消息。

**错误消息格式**：
```
Tool call arguments were truncated (finish_reason=length). The output hit the
max_tokens limit. Try one of:
1. Reduce content size and call write_artifact again.
2. Create the artifact with minimal content first, then use update_artifact
   to add files incrementally.
3. Simplify the content (e.g. inline CSS instead of separate files).
```

**理由**：`finish_reason` 不是所有 provider 都可靠（有些 provider 在截断时不返回 `length`），所以加 buffer 不完整性检查作为兜底。

### D3: Mermaid 校验 — 自动补全 declaration

**选择**：当 Mermaid 源码不以 `flowchart`/`graph`/`sequenceDiagram` 等开头时，尝试推断图类型并自动补全 declaration。

推断规则：
- 含 `-->` 或 `---` → `flowchart TD`
- 含 `->>` 且含 `Note` → `sequenceDiagram`
- 其他 → 返回原来的错误

**理由**：LLM 生成 Mermaid 时经常只写节点关系而忘记 declaration，这不是语法错误而是格式疏漏，应自动修复。

### D4: update_artifact — 直接修改 content_dict，不创建新版本

**选择**：`update_artifact` 直接修改当前 artifact 的 `content_dict`（JSON 列），不创建新版本行。

**参数**：
```python
class _UpdateArgs(BaseModel):
    artifact_id: str = Field(alias="artifactId")
    add_files: dict[str, str] | None = Field(default=None, alias="addFiles")
    update_files: dict[str, str] | None = Field(default=None, alias="updateFiles")
    remove_files: list[str] | None = Field(default=None, alias="removeFiles")
```

**限制**：
- 只接受 `web_app` 类型
- 每次最多 20 个文件操作
- 单文件最大 100KB
- 文件路径必须为相对路径（不含 `..` 或绝对路径）

**理由**：创建新版本会导致前端渲染多个版本卡片，对分片写入场景体验不好。直接修改 content_dict 保持单一版本，用户最终看到的是完整产物。

**替代方案**：创建新版本（走 parentArtifactId 链）。否决：分片写入会产生 N 个中间版本，前端版本列表会很混乱。

### D5: 错误消息结构化

**选择**：`write_artifact._handler` 的校验失败返回结构化错误消息：

```
Invalid content for type '{type}'.
Detail: {specific error}
Expected format: {example for this type}
Received (first 200 chars): {preview}
Tip: Pass content as a JSON object, not a stringified JSON string.
```

每种 type 维护一个期望格式示例字符串。

### D6: 工具描述精简策略

**选择**：将 `_CONTENT_DESCRIPTION` 从 16 行叙述式改为 per-type one-liner + 通用 JSON 反序列化提醒。保留 "不要 JSON 字符串化 content" 的警告（这是最高频错误）。

### D7: 空参数恢复 — 从 text_buffer / reasoning_buffer 提取 JSON

**选择**：当 tool call 的 `args_buffer` 为空时（模型产生了 tool call id + name 但未在 `tool_calls` delta 中产生参数），通过 `_recover_tool_args` 函数尝试从 `text_buffer` 和 `reasoning_buffer` 中恢复参数。

**三种 JSON 提取策略**（`_try_extract_json`）：
1. 直接解析 — text 本身是 JSON 对象（`startswith("{")`）
2. Markdown 代码块 — 从 ` ```json ... ``` ` 或 ` ``` ... ``` ` 中提取
3. 平衡扫描 — 从文本中找到第一个 `{`，扫描到匹配的 `}`，尝试 `json.loads`

**恢复顺序**：先试 `text_buffer`，再试 `reasoning_buffer`（reasoning 模型如 DeepSeek R1 可能将参数放在思考内容中）。

**恢复失败时的处理**：
- `finish_reason == "length"` → emit 截断错误（`_TRUNCATION_ERROR_MSG`）
- 否则 → emit 空参数错误（`_EMPTY_ARGS_ERROR_MSG`），明确告知 LLM 参数未收到

**理由**：reasoning 模型（如 DeepSeek R1）在思考模式下可能将 tool call 参数放在 `content` 或 `reasoning_content` 中，而非 `tool_calls.function.arguments`。原有的恢复逻辑仅检查 `text_buffer.strip().startswith("{")`，无法处理 markdown 代码块和带文字前缀的 JSON。`stream` 方法甚至完全没有恢复逻辑。

**替代方案**：在 AgentRunner 层而非 adapter 层做恢复。否决：adapter 是事件流翻译层，最接近原始数据（text_buffer / reasoning_buffer），在此层恢复最自然。

## Risks / Trade-offs

- **[max_tokens 过大导致 provider 报错]** → 部分模型有硬上限（如 gpt-3.5-turbo 4096 输出上限）。Mitigation：`model_registry` 的 `KNOWN_MODELS` 已记录各模型 context_window，推导时取 `min(动态值, provider_硬上限)`。需要在 `ModelLimits` 中新增 `max_output_tokens` 字段来记录 provider 硬上限。

- **[截断检测的 false positive]** → 非 JSON 字符串可能不以 `}` 结尾但并非截断。Mitigation：只在 `finish_reason == "length"` 时触发截断错误，buffer 不完整性仅作为日志 warning 而非硬判断。

- **[update_artifact 并发冲突]** → 两个 run 同时更新同一 artifact。Mitigation：`get_db()` 的 session 是事务级的，SQLAlchemy 行锁 + `version` 字段乐观锁（已有 version 字段，update 时检查 version 不变）。

- **[Mermaid 自动补全 declaration 推断错误]** → 把 sequence diagram 误推为 flowchart。Mitigation：推断规则保守，只在明确的 `-->` 模式下补 `flowchart TD`，其他情况仍返回错误让 LLM 修正。

- **[错误消息过长增加 token 消耗]** → 结构化错误消息比原来长。Mitigation：控制消息长度，示例只保留一行，收到的内容预览限 200 字符。
