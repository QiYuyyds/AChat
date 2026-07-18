## 1. fs_list — depth 参数

- [x] 1.1 在 `fs_service.py` 中新增 `list_dir_recursive(workspace, path, depth, show_hidden)` 函数，返回扁平 entries 列表（含 `relativePath` / `depth` 字段），递归跳过 `_SKIP_DIRS`，entry 上限 500
- [x] 1.2 在 `fs_list.py` 的 `_Args` 模型中增加 `depth: int = Field(default=1, ge=1, le=5)` 参数
- [x] 1.3 在 `fs_list.py` 的 `_PARAMETERS` JSON schema 中增加 `depth` 字段描述
- [x] 1.4 在 `fs_list.py` 的 `_handler` 中：`depth=1` 时走当前 `list_dir_in_workspace` 路径；`depth>1` 时走新的 `list_dir_recursive` 路径
- [x] 1.5 更新 `fs_list` 工具 `description`，说明 `depth` 参数用途和典型用法（项目结构概览时用 `depth=3`）
- [x] 1.6 更新 `fs_service.py` 的 `ListEntry` dataclass，增加 `relative_path: str | None` 和 `depth: int | None` 可选字段（`depth=1` 时不填，保持兼容）

## 2. fs_list — showHidden 参数

- [x] 2.1 在 `fs_list.py` 的 `_Args` 模型中增加 `showHidden: bool = False` 参数
- [x] 2.2 在 `fs_list.py` 的 `_PARAMETERS` JSON schema 中增加 `showHidden` 字段描述
- [x] 2.3 修改 `fs_service.py` 的 `list_dir_in_workspace`，增加 `show_hidden: bool = False` 参数；`show_hidden=False` 时保持当前 dotfile 过滤行为，`true` 时不过滤
- [x] 2.4 修改 `list_dir_recursive` 同步支持 `show_hidden` 参数
- [x] 2.5 更新 `fs_list` 工具 `description`，提及 `showHidden` 可查看 `.env.example` 等配置文件

## 3. fs_read — mode 参数

- [x] 3.1 在 `fs_read.py` 的 `_Args` 模型中增加 `mode: str = Field(default="full", pattern="^(full|outline|head)$")` 参数
- [x] 3.2 在 `fs_read.py` 的 `_PARAMETERS` JSON schema 中增加 `mode` 字段描述
- [x] 3.3 在 `fs_service.py` 中新增 `extract_outline(content, language)` 函数，用正则提取 import / type / function / variable 签名，返回 `list[dict]`
- [x] 3.4 在 `fs_service.py` 中新增 `detect_language(path)` 函数，按扩展名映射语言（`.ts/.tsx` → typescript, `.py` → python, `.go` → go, `.java` → java, `.rs` → rust, `.js/.jsx` → javascript 等）
- [x] 3.5 在 `fs_read.py` 的 `_handler` 中：`mode="full"` 时走当前路径；`mode="outline"` 时调 `extract_outline` 返回骨架结构（不含 `content`，含 `outline` / `language` / `totalLines` / `fullSize`）；`mode="head"` 时等价于 `offset=0, limit=N`（默认 50 行）
- [x] 3.6 `mode="outline"` 且未提取到任何结构时，返回空 `outline` 数组 + `note` 字段建议 fallback 到 `mode="full"`
- [x] 3.7 更新 `fs_read` 工具 `description`，说明三种模式用途和 token 节省效果

## 4. code_explore — 自动初始化

- [x] 4.1 找到 workspace 绑定 / 创建的 API 路径（后端 `app/api/` 下的 workspace 相关路由）
- [x] 4.2 在 workspace 创建成功后（`mode="local"` 且 `bound_path` 存在时），**无条件**异步调用 `schedule_workspace_enable()`，不阻塞响应。`code_intelligence_enabled` 参数已弃用，保留向后兼容但忽略
- [x] 4.3 确认 `code_intelligence.enabled` settings 开关可关闭自动初始化
- [x] 4.4 增加日志：`[code-intelligence] auto-enable triggered for workspace %s (bound_path=%s, triggered_by=local_workspace_binding)`
- [x] 4.5 前端 `new-conversation-dialog.tsx` 移除手动 `CodeIntelligenceSwitch`，改为信息提示「源码智能将自动启用」
- [x] 4.6 前端 `code-intelligence.ts` 移除 `buildCodeIntelligenceCreateFields` 函数和 `WorkspaceMode` 类型
- [x] 4.7 前端 `api.ts` 的 `CreateConversationBody` 移除 `codeIntelligenceEnabled` 字段

## 5. code_explore — fallback 消息改进

- [x] 5.1 修改 `code_explore.py` 的 `_fallback` 函数，接受 `metadata` 参数以获取当前状态和进度
- [x] 5.2 fallback 消息改为包含：当前状态、建议替代工具（`fs_list(depth=3)` / `fs_grep` / `fs_read(mode="outline")`）、图谱就绪后可用提示
- [x] 5.3 更新 `code_explore` 工具 `description`，提及自动初始化机制

## 6. Spec 文档同步

- [x] 6.1 更新 `specs/07-tools.md` 中 `fs_list` 的签名与行为描述（增加 `depth` / `showHidden` 参数说明）
- [x] 6.2 更新 `specs/07-tools.md` 中 `fs_read` 的签名与行为描述（增加 `mode` 参数说明）
- [x] 6.3 更新 `specs/07-tools.md` 中 `code_explore` 的行为描述（自动初始化 + fallback 消息）

## 7. 测试

- [x] 7.1 `fs_list` depth 测试：`depth=1` 返回格式不变；`depth=3` 返回扁平列表含 `relativePath` / `depth`；递归跳过 `node_modules`；超 500 条 `truncated=true`
- [x] 7.2 `fs_list` showHidden 测试：默认隐藏 dotfiles；`showHidden=true` 包含 `.env.example`
- [x] 7.3 `fs_read` mode 测试：`mode="full"` 返回格式不变；`mode="outline"` 返回骨架不含 `content`；`mode="head"` 返回前 N 行
- [x] 7.4 `fs_read` outline 语言覆盖测试：TypeScript / Python / Go / Java 文件各至少一个测试用例
- [x] 7.5 `code_explore` fallback 消息测试：图谱未就绪时返回指导性提示
- [x] 7.6 回归测试：已有 agent 配置（未使用新参数）行为完全不变
- [x] 7.7 后端 `ruff check .` 和 `pytest` 通过
