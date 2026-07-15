# Proposal: add-file-write-preview

## Why

当 SDK Agent 生成代码或文档时，用户只能看到最终结果——要么是 text part 里的 markdown 代码块（不是"真的在写文件"），要么是 `fs_write` / `write_artifact` 执行完后的产物卡片。整个过程中用户看不到代码"一行行生长"的过程，也看不到文件修改的 diff 痕迹（auto 模式下完全没有 diff，review 模式需要额外点击）。这导致 Agent 工作时缺乏实时反馈和透明度，尤其是长时间代码生成任务中用户只能对着 spinner 等待。

## What Changes

- **新增 `file_write_preview` MessagePart 类型**：在 CustomAdapter 的 tool_call 累积阶段，实时流式渲染即将写入的文件内容（绿色高亮 + 闪烁光标），tool 执行完后自动切换为 diff 渲染（修改已有文件时）或最终代码展示（新建文件时）
- **新增 `file_write_preview.append` PartDelta 类型**：支持 `file_write_preview` part 的流式增量追加
- **新增 `file_write_preview.complete` StreamEvent 类型**：tool 执行完后回填 oldContent / newContent，触发前端从"流式预览"切换到"diff / 最终态"
- **扩展 `fs_write` / `fs_edit` tool result**：auto 和 review 模式都返回 `path` / `oldContent` / `newContent`，让前端 ToolUsePart 也能内联渲染 diff 预览（方向 B）
- **新增前端 `FileWritePreviewPart` 组件**：三态渲染——streaming（绿色高亮代码 + 光标）、complete-with-diff（unified diff 视图）、complete-new-file（最终代码）
- **增强前端 `ToolUsePart` 组件**：当 `fs_write` / `fs_edit` 的 tool result 包含 `oldContent` / `newContent` 时，在工具卡片内联渲染紧凑 diff 预览

## Capabilities

### New Capabilities

- `file-write-preview`: 流式文件写入预览——当 SDK Agent 调用 `fs_write` 时，在消息流中实时渲染文件内容生成过程和修改 diff

### Modified Capabilities

- `message-parts`: 新增 `file_write_preview` part 类型和 `file_write_preview.append` delta 类型
- `stream-events`: 新增 `file_write_preview.complete` 事件类型
- `tools`: `fs_write` 和 `fs_edit` 的 tool result 扩展 diff 数据字段
- `adapters`: CustomAdapter 需要在 tool_call 累积阶段产出 `file_write_preview` part 和 delta 事件
- `frontend`: 新增 `FileWritePreviewPart` 组件，增强 `ToolUsePart` 的 diff 渲染

## Impact

- **后端**：`custom_adapter.py`（新增 partial JSON content 提取状态机）、`agent_runner.py`（consume_stream 处理新事件）、`events.py`（新增事件 schema）、`fs_write.py` / `fs_edit.py`（扩展 tool result）
- **前端**：`types.ts`（新增 part/delta 类型）、`message-parts.tsx`（新增组件 + 增强 ToolUsePart）、`app-store.ts`（reducer 处理新事件）
- **Spec 文档**：`specs/02-stream-events.md`、`specs/03-message-parts.md` 需同步更新
- **兼容性**：新 part 类型和事件是增量添加，旧消息不受影响；前端 reducer 对未知 part type 走 `default: return null`，不会报错
