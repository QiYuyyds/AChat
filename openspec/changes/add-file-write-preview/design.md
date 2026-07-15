# Design: add-file-write-preview

## Context

AChat 的 SDK Agent（CustomAdapter）在生成代码或文档时，用户缺乏实时反馈。当前流程：

1. LLM 在 text part 里描述"我来写这个文件"（流式可见）
2. LLM 在同一个 turn 里发起 `fs_write` / `write_artifact` tool_call
3. tool_call 的 `args_buffer` 在流式累积（但用户看不到内容）
4. Turn 结束后 tool 执行，一次性产出结果

用户在步骤 3 期间只能看到 spinner，无法感知"Agent 正在写什么"。

此外，`fs_write` / `fs_edit` 在 auto 模式下的 tool result 不包含 `oldContent` / `newContent`，前端无法展示修改痕迹。

## Goals / Non-Goals

**Goals:**

- SDK Agent 调用 `fs_write` 时，用户能实时看到文件内容"一行行生长"
- 修改已有文件时，流式预览结束后自动切换为 diff 视图
- `fs_write` / `fs_edit` 在 auto 模式下 tool result 也携带 diff 数据，前端可在 ToolUsePart 内联渲染 diff
- 新增的 part 类型和事件与现有架构模式对称（类比 `artifact_ref` 注入路径、`execution_plan` 状态更新路径）
- 降级安全：partial JSON 提取失败时，`file_write_preview` part 停留在空/不完整状态，tool 执行完后正常显示 diff

**Non-Goals:**

- 不覆盖 `write_artifact` 工具的流式预览（Phase 1 只做 `fs_write`；`write_artifact(document)` 的 `content` 字段可后续迭代，`write_artifact(web_app)` 多文件场景暂不做）
- 不覆盖 CLI Agent（Claude Code / Codex）——CLI 走子进程事件翻译，tool_call 不是 SDK 累积模式
- 不覆盖 `fs_edit` 的流式预览——`fs_edit` 的 `old_string` / `new_string` 是短片段替换，不适合流式预览整个文件（但方向 B 的 diff 痕迹覆盖 `fs_edit`）
- 不做 split diff 视图（内联场景用 unified diff 更紧凑）
- 不改变 LLM 的输出方式（不用 text pattern 替代 tool_call）

## Decisions

### D1: 新增 `file_write_preview` MessagePart — 独立 part，非 tool_use 子渲染

**选择**：`file_write_preview` 作为独立 part 类型，出现在 tool_use part 之前（因为 preview 在 tool_call 累积阶段就开始产出）。

**备选**：
- A) 作为 ToolUsePart 的子渲染（内嵌在工具卡片里） → 用户需要展开工具卡片才能看到预览，降低了"流式代码生长"的视觉冲击力
- B) 替换 text part 里的代码块 → 破坏了 text part 和 tool_call 的独立性

**理由**：独立 part 与 `execution_plan`、`artifact_ref` 模式对称——都是"工具产出 → Runner/Adapter 注入独立 part"。用户在消息流中直接看到代码在"生长"，不需要额外交互。

### D2: 从 partial args_buffer 提取 content — 轻量 JSON 字符串状态机

**选择**：在 CustomAdapter 中，当 `tcd.function.name == "fs_write"` 确认后，用一个状态机追踪 `args_buffer` 的累积，提取 `content` 字段的增量。

**状态机**：
```
IDLE → 检测到 "content" key → FOUND_KEY
FOUND_KEY → 检测到 : → WAIT_VALUE
WAIT_VALUE → 检测到 " → IN_STRING (开始提取)
IN_STRING:
  - \" → ESCAPE → 解码后回到 IN_STRING
  - " → DONE (提取完毕)
  - 其他 → 累积为 content chunk → yield part.delta
ESCAPE:
  - n → \n, t → \t, " → ", \\ → \, / → /, r → \r
  - uXXXX → Unicode 转义
  → 回到 IN_STRING
```

**备选**：
- A) 不提取，等 turn 结束后一次性显示 → 失去"流式"意义
- B) 改变 LLM 输出方式，让代码走 text part → 破坏 tool_call 协议
- C) 用 `json.JSONDecoder.raw_decode` 解析 partial JSON → 标准 JSON parser 无法处理不完整 JSON

**理由**：状态机只追踪一个已知字段 `"content"` 的字符串值边界，不需要完整 JSON 解析。大多数模型输出的 `fs_write` args JSON 格式是 `{"path":"...", "content":"..."}` ，`content` 通常是最后一个字段。如果提取失败（模型格式异常），降级到空 preview，tool.result 照常工作。

### D3: path 提取策略 — 从 args_buffer 尽早解析

**选择**：当状态机检测到 `"path"` key 时，先提取 `path` 字段值用于 `part.start` 的 `path` 属性。如果 `path` 还未提取到，`part.start` 中 `path` 先设为空字符串，待提取到后通过 `file_write_preview.complete` 事件补全。

**理由**：`path` 通常出现在 `content` 之前，大多数情况下能及时提取到。即使提取不到，part 的 path 为空也不影响功能——前端显示"正在写入..."而非文件名。

### D4: Tool 执行完后的状态回填 — `file_write_preview.complete` 事件

**选择**：在 `_execute_tool_call_to_result` 中，当 `fs_write` / `fs_edit` 执行成功时，追加 `FileWritePreviewCompleteEvent`，携带 `path` / `oldContent` / `newContent` / `status`。`consume_stream` 收到后更新 `parts_buffer` 中的 `file_write_preview` part 并发布 SSE 事件。

**流程**（对称于 `artifact.create` → `artifact_ref` 注入路径）：
```
1. CustomAdapter: tcd.function.name == "fs_write" → yield part.start(file_write_preview)
2. CustomAdapter: args_buffer 增量 → yield part.delta(file_write_preview.append)
3. AgentRunner: _execute_tool_call_to_result → fs_write 成功 → 追加 FileWritePreviewCompleteEvent
4. consume_stream: 收到 file_write_preview.complete → 更新 parts_buffer 中对应 part + SSE publish
5. 前端: reducer 收到 file_write_preview.complete → 更新 part 的 status/oldContent/newContent
6. 前端: FileWritePreviewPart 根据 status 切换渲染模式
```

**理由**：和 `artifact.create` → `artifact_ref`、`plan.created` → `execution_plan` 完全对称。事件驱动，不破坏现有架构。

### D5: file_write_preview part 与 tool_use part 的对应关系

**选择**：通过 `callId` 字段关联。`file_write_preview` part 携带 `callId` 字段，与后续的 `tool_use` part 的 `callId` 一致。前端可据此将两者视觉上关联（Phase 1 不做强关联，各自独立渲染）。

**理由**：保留扩展性。未来可以在 ToolUsePart 里引用对应的 preview part，或合并渲染。Phase 1 各自独立渲染已经够用。

### D6: 方向 B — fs_write / fs_edit tool result 扩展 diff 数据

**选择**：`fs_write` 和 `fs_edit` 在 auto 和 review 模式下的 tool result 都新增 `path` / `oldContent` / `newContent` 字段。

**当前 auto 模式返回**：
```python
ok({ "path": ..., "absolutePath": ..., "bytes": ..., "applied": "auto" })
```

**扩展后**：
```python
ok({
    "path": ...,
    "absolutePath": ...,
    "bytes": ...,
    "applied": "auto",
    "oldContent": old_content,     # 新增：修改前内容（新文件为 null）
    "newContent": new_content,     # 新增：修改后内容
})
```

`fs_edit` 同理。

**备选**：
- A) 只在 auto 模式加 diff 数据 → review 模式的 diff 走 `fs_write.pending` 事件，但 ToolUsePart 统一渲染更简单
- B) 不改 tool result，单独发 diff 事件 → 增加事件类型复杂度

**理由**：统一在 tool result 里返回 diff 数据最简单。前端 ToolUsePart 只需检查 `result.oldContent` / `result.newContent` 即可决定是否渲染 diff。review 模式下 tool result 也带 diff 数据，可让 ToolUsePart 展示"已应用/已拒绝"的 diff 摘要。

### D7: 前端 ToolUsePart 内联 diff 渲染方式 — 紧凑 unified diff

**选择**：当 ToolUsePart 检测到 `fs_write` / `fs_edit` 的 result 包含 `oldContent` / `newContent` 时，在工具卡片内嵌一个紧凑 unified diff 预览（最多显示 8 行，超出折叠）。

**理由**：unified diff 比单行摘要信息量大，比 split diff 紧凑。8 行足以展示修改的核心差异。点击展开可看完整 diff。

### D8: file_write_preview 仅在 call_once 路径生效

**选择**：方向 A 的流式预览仅在 `_run_react_loop`（SDK Agent 的 call_once 路径）中通过 CustomAdapter 产出。`stream` 模式（旧版 CustomAdapter 自循环）不产出 preview。

**理由**：`stream` 模式是旧版接口，新 Agent 都走 `_run_react_loop`。`stream` 模式的 tool 执行在 Adapter 内部完成，与 `consume_stream` 的注入路径不兼容。方向 B（tool result diff 数据）在两种模式下都生效。

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| partial JSON 提取失败（模型输出异常格式） | 状态机提取失败时降级：`file_write_preview` part 为空，tool 执行完后正常显示 diff。零风险降级 |
| JSON 字符串转义处理遗漏（如 `\uXXXX` Unicode 转义） | 状态机覆盖常见转义序列；未覆盖的转义原样输出，不影响代码可读性 |
| `content` 字段不是 args 的最后一个字段 | 状态机在检测到 `"content"` key 后开始提取，不依赖字段顺序 |
| file_write_preview part 占用 partIndex，与后续 tool_use part 的 index 不连续 | PartList 按 part 数组索引渲染，不要求连续。与 thinking/text/tool_use 交错同理 |
| 大文件 content 导致 preview part 过长 | preview 只展示最近 N 行（前端限制渲染行数）；tool.result 正常落库不受影响 |
| diff 数据增加 tool result 体积 | oldContent/newContent 可能较大（百 KB 级）。Phase 1 不做截断，由前端虚拟化渲染处理 |
| 同时有 file_write_preview part 和 ToolUsePart 显示同一文件 | file_write_preview 展示"流式过程 + 最终 diff"，ToolUsePart 展示"工具调用元数据 + 简要 diff"。内容有重叠但信息层次不同 |

## Migration Plan

无需迁移。新 MessagePart 类型和事件是增量添加：

1. `file_write_preview` part 是新类型，旧消息不含此 part
2. `file_write_preview.append` delta 是新 delta 类型，旧 reducer 不匹配时走 `default` 忽略
3. `file_write_preview.complete` 事件是新事件类型，前端未实现的 case 走 `default: ignore`
4. `fs_write` / `fs_edit` tool result 新增字段是向后兼容扩展（新增字段，不删改旧字段）
5. 前端 reducer 对未知 part type 走 `default: return null`，不会报错

部署顺序：后端先部署（新事件/新字段对旧前端无影响），前端后部署。

## Open Questions

- `write_artifact(document)` 的 `content` 字段是否纳入 Phase 1 流式预览？当前设计不含，但 `document` 类型结构与 `fs_write` 类似，实现成本很低
- `file_write_preview` part 与 ToolUsePart 是否需要在 Phase 1 就做视觉关联（如合并渲染）？当前设计各自独立
- diff 数据较大时是否需要截断 `oldContent` / `newContent`？还是完全依赖前端虚拟化？
