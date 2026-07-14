### Requirement: build_artifact_content SHALL accept extended key aliases

各 `_build_*` 函数 MUST 接受 LLM 常见的关键字变体：

| 类型 | 现有 key | 新增 key |
|---|---|---|
| web_app | files, html, css, js, content, code | src, body |
| document | content, markdown, text | body, html (HTML 转 markdown 纯文本) |
| image | url, alt | src, link |
| ppt | slides | pages (单个 slide dict 自动包成数组) |
| diagram | source, mermaid, code, content | graph |

#### Scenario: web_app 使用 src key
- **WHEN** LLM 传 `content: { "src": "<html>...</html>" }` 且 `type: "web_app"`
- **THEN** `build_artifact_content` 返回 `{ "type": "web_app", "files": { "index.html": "<html>...</html>" }, "entry": "index.html" }`

#### Scenario: document 使用 body key
- **WHEN** LLM 传 `content: { "body": "# Title" }` 且 `type: "document"`
- **THEN** 返回 `{ "type": "document", "format": "markdown", "content": "# Title" }`

#### Scenario: ppt 传单个 slide dict 而非数组
- **WHEN** LLM 传 `content: { "title": "Deck", "slides": { "title": "Slide 1" } }`（slides 是 dict 而非 list）
- **THEN** 自动包成数组 `[{"title": "Slide 1"}]`
- **AND** 正常创建 artifact

### Requirement: Mermaid normalization SHALL auto-repair common LLM output patterns

`normalise_mermaid_source` MUST 增强以下自动修复能力：

1. **自动补全 declaration**：当源码不以已知 declaration 开头时，尝试推断图类型并前置 declaration
   - 含 `-->` 或 `---` → 前置 `flowchart TD\n`
   - 其他 → 仍返回错误
2. **多行围栏剥离**：`_FENCE_RE` MUST 支持围栏前后有空白字符
3. **Unicode label 支持**：`_NODE_LABEL_RE` MUST 匹配含中文、日文、韩文等 Unicode 字符的 label

#### Scenario: 缺失 declaration 自动补全
- **WHEN** LLM 生成 Mermaid 源码 `A[Start] --> B[End]`（无 `flowchart TD` 前缀）
- **THEN** `normalise_mermaid_source` 自动补全为 `flowchart TD\nA["Start"] --> B["End"]`
- **AND** 返回 `ok=True`

#### Scenario: 带 Markdown 围栏自动剥离
- **WHEN** LLM 生成源码包含 ` ```mermaid\nflowchart TD\nA --> B\n``` `
- **THEN** 围栏被剥离
- **AND** 返回 `ok=True`

#### Scenario: 中文 label 不被拒绝
- **WHEN** LLM 生成 `flowchart TD\nA[开始] --> B[结束]`
- **THEN** label 被正确加引号：`A["开始"] --> B["结束"]`
- **AND** 返回 `ok=True`

#### Scenario: 无法推断图类型仍返回错误
- **WHEN** 源码为 `some random text without diagram syntax`
- **AND** 无法推断图类型
- **THEN** 返回 `ok=False` 带原有错误消息
