### Requirement: write_artifact SHALL return actionable error messages

`write_artifact` 的 handler 在校验失败时 MUST 返回结构化错误消息，包含以下部分：
1. 错误类型描述（缺失字段 / 不支持的类型 / content 格式不匹配）
2. 期望格式示例（per-type 一行 JSON 示例）
3. 收到的内容预览（前 200 字符）
4. 通用修复提示（如 "pass content as JSON object, not stringified JSON"）

#### Scenario: 缺失必填字段
- **WHEN** LLM 调用 `write_artifact` 但缺少 `type`、`title` 或 `content` 字段
- **THEN** 返回错误消息列出缺失字段名
- **AND** 消息包含 "Required fields: type, title, content"

#### Scenario: content 格式不匹配
- **WHEN** LLM 传 `type="web_app"` 但 content 缺少 `files` 字段
- **THEN** 返回错误消息包含 "Invalid content for type 'web_app'"
- **AND** 消息包含 web_app 的期望格式示例
- **AND** 消息包含收到的 content 预览（前 200 字符）

#### Scenario: content 被错误地 JSON 字符串化
- **WHEN** LLM 传 `content` 为字符串 `'{"format":"markdown","content":"# Hi"}'`
- **AND** `_unwrap_stringified_content` 成功解析
- **THEN** 正常创建 artifact（已有行为，不改变）

### Requirement: write_artifact tool description SHALL be concise

`_CONTENT_DESCRIPTION` SHALL 精简为 per-type one-liner 格式示例 + JSON 反序列化提醒。总长度不超过 10 行。每种 type 的格式示例 MUST 为单行 JSON。

#### Scenario: LLM 理解工具描述
- **WHEN** LLM 读取 `write_artifact` 的 tool schema
- **THEN** `content` 参数的 description 包含所有 5 种 type 的格式示例
- **AND** 每种 type 的示例为单行
- **AND** description 包含 "do NOT JSON-stringify content" 警告

### Requirement: update_artifact tool SHALL support incremental file updates

新增 `update_artifact` 工具，用于向已有 `web_app` artifact 追加、修改或删除文件。

**参数**：
- `artifactId`（必填）：目标 artifact ID
- `addFiles`（可选）：`Record<string, string>`，新增文件
- `updateFiles`（可选）：`Record<string, string>`，覆盖已有文件
- `removeFiles`（可选）：`string[]`，删除文件

**约束**：
- 只接受 `type == "web_app"` 的 artifact
- 每次调用最多 20 个文件操作（add + update + remove 总数）
- 单文件最大 100KB
- 文件路径必须为相对路径，不含 `..` 或绝对路径分隔符
- 直接修改当前 artifact 的 `content_dict`，不创建新版本

#### Scenario: 向已有 web_app 追加文件
- **WHEN** Agent 调用 `update_artifact({ artifactId: "art_123", addFiles: { "style.css": "body {}" } })`
- **AND** artifact `art_123` 存在且 type 为 `web_app`
- **THEN** artifact 的 `content_dict.files` 新增 `style.css` 条目
- **AND** 返回 `{ artifactId: "art_123", updatedFiles: ["style.css"] }`

#### Scenario: 更新已有文件
- **WHEN** Agent 调用 `update_artifact({ artifactId: "art_123", updateFiles: { "index.html": "<new content>" } })`
- **AND** artifact 已有 `index.html` 文件
- **THEN** `content_dict.files["index.html"]` 被新内容覆盖

#### Scenario: 删除文件
- **WHEN** Agent 调用 `update_artifact({ artifactId: "art_123", removeFiles: ["old.js"] })`
- **THEN** `content_dict.files` 中 `old.js` 被移除

#### Scenario: 非 web_app 类型被拒绝
- **WHEN** Agent 对 `type == "document"` 的 artifact 调用 `update_artifact`
- **THEN** 返回错误 "update_artifact only supports web_app type"

#### Scenario: artifact 不存在
- **WHEN** Agent 调用 `update_artifact({ artifactId: "art_not_exist" })`
- **THEN** 返回错误 "Artifact not found: art_not_exist"

#### Scenario: 文件数超限
- **WHEN** 单次调用 add + update + remove 总数超过 20
- **THEN** 返回错误 "Too many file operations (max 20 per call)"
