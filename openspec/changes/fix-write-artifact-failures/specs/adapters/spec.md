### Requirement: CustomAdapter SHALL set max_tokens on LLM API calls

CustomAdapter 的 `call_once` 和 `stream` 方法在调用 `client.chat.completions.create` 时 MUST 传入 `max_tokens` 参数。该值 SHALL 从 `get_model_limits(provider, model_id)` 动态推导：`max_tokens = max(output_reserve, context_window - estimated_input_tokens - 512)`，但不得超过 provider 的硬上限（`ModelLimits.max_output_tokens`，若已知）。

#### Scenario: LLM 输出不超过 max_tokens
- **WHEN** Agent 调用 `write_artifact` 创建一个中等大小的 web_app（约 3000 tokens）
- **AND** 模型 context_window=64000, 估算输入=8000 tokens
- **THEN** `max_tokens` 设为 `max(4096, 64000 - 8000 - 512) = 55488`
- **AND** LLM 输出不被截断
- **AND** tool_call args JSON 完整解析成功

#### Scenario: 模型有硬上限时 max_tokens 不超过硬上限
- **WHEN** 模型 `gpt-3.5-turbo`（context_window=16385, max_output_tokens=4096）
- **AND** 估算输入=4000 tokens
- **THEN** `max_tokens` 设为 `min(max(4096, 16385 - 4000 - 512), 4096) = 4096`

### Requirement: CustomAdapter SHALL detect truncated tool call arguments

当 `json.loads(tc.args_buffer)` 失败时，CustomAdapter MUST 检查是否为输出截断导致：
- 若 `finish_reason == "length"`，MUST 判定为截断
- 若 `args_buffer` 非空但不以 `}` 或 `]` 结尾，SHALL 记录 warning 日志

截断时 MUST 不传空 `{}` 给工具执行，而是直接 emit `ToolResultEvent` 带明确截断错误消息，消息内容 MUST 包含修复建议（拆分内容、使用 `update_artifact` 分片写入）。

#### Scenario: 输出截断导致 JSON 断裂
- **WHEN** LLM 生成大型 web_app 产物，输出超过 max_tokens
- **AND** `finish_reason` 返回 `"length"`
- **AND** `args_buffer` 为不完整的 JSON 字符串
- **THEN** 不执行 `write_artifact` 工具
- **AND** emit `ToolResultEvent` 带截断错误消息
- **AND** 错误消息包含 "truncated" 和 "update_artifact" 关键字

#### Scenario: 非 truncation 的 JSON 解析失败
- **WHEN** `finish_reason` 不是 `"length"` 且 `args_buffer` 以 `}` 结尾但 JSON 无效
- **THEN** 保持现有行为：`args = {}`
- **AND** 记录 warning 日志
