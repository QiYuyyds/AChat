## Why

Agent 面临「分析项目代码」类探索型任务时，只能用 `fs_list` 逐目录遍历、`fs_read` 逐文件读取完整内容。这导致：上下文快速膨胀（一轮 10 次 `fs_read` ≈ 125k tokens）、触发结构性压缩后丢失文件内容记忆、跨 run 后产生幻觉（猜测不存在的文件路径）。同时 `code_explore` 在项目绑定时不可用（图谱未就绪），`fs_list` 隐藏所有 dotfiles 丢失项目元信息。根本原因是现有工具缺少「结构概览」和「轻量读取」能力。

## What Changes

- **`fs_list` 增加 `depth` 参数**（1–5，默认 1）：`depth=1` 保持当前行为（列出单个目录）；`depth>1` 递归展开子目录，返回扁平 entries 列表，每个 entry 携带 `relativePath` 和 `depth` 字段。自动跳过依赖目录（`node_modules` / `.git` / `__pycache__` 等）。entry 总数上限 500，超过时 `truncated=true`。
- **`fs_list` 增加 `showHidden` 参数**（默认 `false`）：当前硬编码隐藏所有 dotfiles，丢失 `.env.example` / `.eslintrc` 等关键项目元信息。改为可选参数，默认保持当前行为，Agent 可显式请求查看隐藏文件。
- **`fs_read` 增加 `mode` 参数**（`"full"` | `"outline"` | `"head"`，默认 `"full"`）：
  - `full`：当前行为（完整内容，≤50k chars）。
  - `outline`：只返回文件结构骨架（import / class / interface / function 签名 + docstring），用纯 Python 正则提取，不调 LLM。token 消耗约为 full 的 1/10。
  - `head`：只读前 N 行（配合 `limit` 参数），适合快速判断文件是否值得完整读取。
- **`code_explore` 自动初始化**：绑定本地项目文件夹时自动触发 `schedule_enable()`，在后台开始构建代码图谱。`code_explore` 不可用时的 fallback 消息改为指导性提示（告知当前状态 + 建议使用的替代工具）。
- **同步更新工具描述**：`fs_list` 和 `fs_read` 的 description 需体现新参数能力，引导 Agent 在项目分析场景优先使用 `depth>1` 和 `mode="outline"`。

## Capabilities

### New Capabilities

（无新 capability——本次变更全部落在已有 `tools` capability 的增量修改上。）

### Modified Capabilities

- `tools`：`fs_list` 增加 `depth` 和 `showHidden` 参数；`fs_read` 增加 `mode` 参数；`code_explore` 在绑定项目时自动初始化且 fallback 消息改为指导性提示。这些是 spec 级别的行为变更，需要 delta spec。

## Impact

- **后端**：
  - `backend/app/tools/fs_list.py`：增加 `depth` / `showHidden` 参数，扩展 handler 逻辑。
  - `backend/app/tools/fs_read.py`：增加 `mode` 参数，新增 outline 正则提取逻辑。
  - `backend/app/services/fs_service.py`：`list_dir_in_workspace` 和 `read_file_in_workspace` 需支持新参数或新增辅助函数。
  - `backend/app/tools/code_explore.py`：改进 fallback 消息。
  - `backend/app/code_intelligence/`：绑定项目时自动触发 `schedule_enable()` 的调用点（可能在 workspace 创建 / 绑定 API 路径中）。
- **Spec 文档**：`specs/07-tools.md` 中 `fs_list` / `fs_read` / `code_explore` 的签名与行为描述需同步更新。
- **API / 事件**：工具 schema 变更通过 adapter 透传给 LLM，不涉及 REST API 或 SSE 事件契约变更。
- **依赖**：无新第三方依赖（outline 模式用纯 Python `re` 模块）。
- **前端**：无 UI 变更（工具结果 JSON 结构兼容扩展，前端不需要改动）。
- **向后兼容**：所有新参数均有默认值，默认行为与当前完全一致，不影响已有 agent 配置。
