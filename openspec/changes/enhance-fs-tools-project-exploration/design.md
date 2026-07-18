## Context

AChat 的文件工具集（`fs_list` / `fs_read` / `fs_glob` / `fs_grep`）当前只支持单目录列举和单文件完整读取。Agent 面临「分析项目代码」类探索型任务时，只能逐目录 `fs_list` + 逐文件 `fs_read` 完整内容，导致：

1. **上下文爆炸**：一个 50k 字符文件 ≈ 12.5k tokens，一轮 10 次 `fs_read` ≈ 125k tokens 直接塞入 messages。
2. **结构性压缩丢信息**：上下文达 90% 时触发 `_mid_run_compact`，旧 tool result 替换为 `"[tool_result 已裁剪]"`，Agent 丢失已读文件的全部内容。
3. **跨 run 幻觉**：新 run 加载历史时 `prune_old_tool_results` 裁剪 3 轮前的 tool result，Agent 知道调过 `fs_read("src/App.tsx")` 但不知道内容，开始猜测文件路径。
4. **dotfiles 丢失**：`fs_list` 硬编码隐藏所有以 `.` 开头的文件，丢失 `.env.example` / `.eslintrc` 等项目元信息。
5. **code_explore 冷启动**：绑定项目时 `code_explore` 不可用（图谱状态非 `ready`），fallback 消息无指导性。

已有工具：`fs_glob`（扁平文件列表 max 200）、`fs_grep`（正则搜索 max 100）、`code_explore`（代码图谱问答，需图谱就绪）、`bash`（shell 命令，10k 截断、跨平台差异、审批开销）。

## Goals / Non-Goals

**Goals:**

- `fs_list` 支持递归展开子目录（`depth` 参数），一次调用获取项目结构概览
- `fs_list` 支持显式请求查看隐藏文件（`showHidden` 参数）
- `fs_read` 支持 outline 模式（只返回 import / type / function 签名，正则提取不调 LLM）
- `fs_read` 支持 head 模式（只读前 N 行）
- `code_explore` 在项目绑定时自动后台初始化，fallback 消息改为指导性提示
- 所有新参数默认值保持当前行为完全一致（向后兼容）

**Non-Goals:**

- 不新增工具（不加 `fs_tree` / `fs_read_batch` / `fs_read_outline` 等独立工具）
- 不改上下文压缩策略（`_mid_run_compact` / `prune_old_tool_results` / `fold_old_messages` 的裁剪逻辑不在本次范围）
- 不改跨 run 的分析状态持久化机制（ProjectMap 概念不在本次范围）
- 不改 `fs_glob` / `fs_grep` / `bash` 的现有行为
- 不引入 LLM-backed 文件摘要（outline 纯正则提取）
- 不改前端 UI（工具结果 JSON 兼容扩展，前端无需改动）

## Decisions

### D1. fs_list depth：扁平列表而非嵌套树

- **选择**：`depth>1` 时返回扁平 entries 列表，每个 entry 携带 `relativePath` 和 `depth` 字段，而非嵌套 `children` 树结构。
- **理由**：
  - JSON 嵌套越深 token 开销越大（`"children":` key 重复 N 次）。
  - LLM 解析嵌套树结构更容易出错；扁平列表 + `depth` 字段更直观。
  - 每个 entry 独立，便于 Agent 按路径/深度过滤理解。
- **返回格式**：
  ```json
  {
    "relPath": "src",
    "entries": [
      {"name": "App.tsx", "relativePath": "src/App.tsx", "isDirectory": false, "size": 2340, "depth": 1},
      {"name": "components", "relativePath": "src/components", "isDirectory": true, "depth": 1},
      {"name": "Chat.tsx", "relativePath": "src/components/Chat.tsx", "isDirectory": false, "size": 5600, "depth": 2}
    ],
    "truncated": false
  }
  ```

### D2. fs_list depth 上限 5，entry 总数上限 500

- **选择**：`depth` 范围 1–5；entries 总数上限 500，超过时 `truncated=true`。
- **理由**：
  - 5 层覆盖绝大多数项目结构（根 → src → components → 子模块 → 文件）。
  - 500 条 entries 足够理解中型项目结构；超大项目 Agent 应缩小 `path` 范围。
  - 防止 Agent 不小心 `depth=999` 遍历整个 `node_modules`。

### D3. fs_list 自动跳过依赖目录

- **选择**：`depth>1` 递归时自动跳过 `_SKIP_DIRS`（`node_modules` / `.git` / `.venv` / `__pycache__` / `.next` / `dist` / `build`），复用 `fs_grep` 已有的集合。
- **理由**：这些目录对「分析项目」没有价值，只会浪费 token。`depth=1` 时仍列出这些目录名（让 Agent 知道它们存在），只是不递归展开。

### D4. fs_list showHidden 默认 false

- **选择**：新增 `showHidden` 布尔参数，默认 `false`（保持当前行为）。`true` 时列出 dotfiles。
- **理由**：默认行为不变保证兼容性。Agent 需要查看 `.env.example` 等文件时可显式 `showHidden=true`，或直接用 `fs_read` 读取（`fs_read` 不受 dotfile 限制）。

### D5. fs_read mode="outline"：纯正则提取

- **选择**：outline 模式用 Python `re` 模块提取文件结构骨架，不调 LLM。
- **提取范围**：
  - `import` / `require` / `from...import` / `#include` 语句
  - `class` / `interface` / `type` / `enum` 定义
  - `function` / `def` / `func` / `async function` 定义（含参数签名）
  - 顶层 `const` / `let` / `var` / `val` 声明
  - 紧跟定义的 docstring / 注释块（首行）
- **返回格式**：
  ```json
  {
    "path": "src/App.tsx",
    "mode": "outline",
    "language": "typescript",
    "outline": [
      {"type": "import", "line": 1, "content": "import React from 'react'"},
      {"type": "function", "line": 15, "content": "function App(): JSX.Element"},
      {"type": "variable", "line": 30, "content": "const routes: Route[] = [...]"}
    ],
    "totalLines": 234,
    "fullSize": 6800
  }
  ```
- **语言检测**：按文件扩展名映射（`.ts/.tsx` → typescript, `.py` → python, `.go` → go, `.java` → java, `.rs` → rust 等）。未识别扩展名时尝试通用正则。

### D6. fs_read mode="head"：复用现有 offset/limit 机制

- **选择**：head 模式等价于 `offset=0, limit=N` 的快捷方式。当 `mode="head"` 且 `limit` 未指定时，默认读取前 50 行。
- **理由**：不引入新逻辑，只是语义糖 + 更友好的返回格式（显式标注 `truncated=true` 和 `totalLines`）。

### D7. code_explore 自动初始化：绑定项目时触发

- **选择**：在 workspace 绑定 / 创建 API 路径中，当 `workspace.mode == "local"` 且 `workspace.bound_path` 存在时，自动调用 `schedule_workspace_enable()`。
- **异步**：图谱构建在后台进行，不阻塞 API 响应。用户首次对话时图谱可能未就绪，但后续对话可用。
- **fallback 消息改进**：
  ```
  当前: "Source intelligence is unavailable (state: indexing). 
         Fall back to the available file search/read tools."
  改为: "代码图谱正在后台构建中（当前状态: indexing, 进度: 45%）。"
        "当前建议使用 fs_list(depth=3) 获取项目结构概览，"
        "用 fs_grep 搜索符号定义，用 fs_read(mode='outline') 查看文件结构骨架。"
        "图谱就绪后可用 code_explore 获取调用链分析。"
  ```

### D8. 不加批量读取参数

- **选择**：不给 `fs_read` 加 `paths: list[str]` 参数。
- **理由**：系统已支持并行工具调用（一次回复中多个 `fs_read` 调用，`asyncio.gather` 并行执行）。并行调用比批量参数更优：每条 tool message 独立（压缩时可独立裁剪）、缓存粒度更细（`tool_call_cache` 按 path 分别命中）。

## Risks / Trade-offs

- **outline 正则覆盖不全**：纯正则无法处理所有语言的语法变体（如 Python 装饰器、Rust 宏、JSX 内联组件）。**缓解**：未匹配到任何结构时返回空 outline + 提示 Agent fallback 到 `mode="full"`；正则模式可后续迭代。
- **depth 递归性能**：超大目录树递归可能慢。**缓解**：跳过依赖目录 + 500 entry 上限 + 可选超时。
- **code_explore 自动初始化的副作用**：用户可能不希望后台跑索引。**缓解**：可通过 `code_intelligence.enabled` settings 开关关闭；自动触发只在 `local` mode workspace 生效。
- **工具 schema 变更对已有 agent 的影响**：新参数有默认值，已有 agent 的 tool 配置不需要改动。LLM 会自动发现新参数（schema 透传）。
