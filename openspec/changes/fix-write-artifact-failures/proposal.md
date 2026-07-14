# Proposal: fix-write-artifact-failures

## Why

`write_artifact` 是 Agent 创建产物（网页、文档、图表、PPT）的唯一入口，但在日常使用中频繁失败。Custom（SDK）路径下，工具调用报红率居高不下，根因涉及多个层面：LLM 输出截断导致参数 JSON 断裂后被静默替换为空对象、Mermaid 校验过于严格、content 格式容错不足、错误消息无法帮助 LLM 自修复、以及大型产物无法分片写入。这些问题累积导致用户体验严重受损。

## What Changes

### Phase 1 — 止血（高频失败）
- **设置 `max_tokens`**：CustomAdapter 的 `call_once` 和 `stream` 方法在调用 LLM API 时传入 `max_tokens`，从 `model_registry` 的上下文窗口动态推导，避免 LLM 输出被 provider 默认值截断
- **截断检测**：当 `finish_reason == "length"` 且 tool_call args JSON 解析失败时，不静默替换为 `{}`，而是返回明确的截断错误消息，指导 LLM 拆分内容或使用 `update_artifact`
- **错误消息增强**：`write_artifact` 的校验失败消息附带期望格式示例和收到的内容预览，让 LLM 下一轮能正确重试

### Phase 2 — 容错（边缘格式）
- **Mermaid 校验增强**：自动补全缺失的 diagram declaration、增强多行围栏剥离、扩展 Unicode label 支持
- **Content key 别名扩展**：各 `_build_*` 函数增加更多常见 key 别名（`src`/`body`/`pages`/`deck` 等），提高对 LLM 输出变体的兼容性
- **工具描述精简**：将 `_CONTENT_DESCRIPTION` 从 16 行长文精简为 per-type one-liner + 通用示例，降低 LLM 理解成本

### Phase 3 — 增强（根除 token 限制）
- **新增 `update_artifact` 工具**：支持向已有 `web_app` artifact 追加/修改/删除文件，使大型产物可以分片写入，避免单次 tool call 超出 token 限制

## Capabilities

### New Capabilities

无。`update_artifact` 是 `tools` 能力的新增工具，不构成独立能力。

### Modified Capabilities

- `adapters`: CustomAdapter 需在 API 调用时设置 `max_tokens` 并检测输出截断
- `tools`: 新增 `update_artifact` 工具；`write_artifact` 错误消息格式变更；工具描述精简
- `artifacts`: `build_artifact_content` 容错增强；Mermaid 校验规则放宽

## Impact

- **后端代码**：
  - `backend/app/adapters/custom_adapter.py` — `call_once` + `stream` 增加 `max_tokens` 和截断检测
  - `backend/app/utils/model_registry.py` — 新增 `max_output_tokens` 字段
  - `backend/app/utils/mermaid_normalize.py` — 校验逻辑增强
  - `backend/app/services/artifact_service.py` — 各 `_build_*` key 别名扩展
  - `backend/app/tools/write_artifact.py` — 错误消息增强 + 描述精简
  - `backend/app/tools/update_artifact.py` — 新文件
  - `backend/app/tools/registry.py` — 注册 `update_artifact`
  - `backend/app/services/agent_runner.py` — tool guidance 增加 `update_artifact` 说明
- **测试**：新增截断场景、容错格式、`update_artifact` 的测试用例
- **Spec 文档**：`specs/04-artifacts.md`、`specs/07-tools.md`、`specs/05-adapter-interface.md` 同步更新
- **无破坏性变更**：所有改动向后兼容，不影响已有 artifact 数据
